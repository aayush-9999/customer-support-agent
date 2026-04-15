# backend/services/conversation_store.py

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update,text
from sqlalchemy.orm import selectinload

from backend.core.config import get_settings
from backend.models.conversation import Conversation, ConversationMessage

logger   = logging.getLogger(__name__)
settings = get_settings()

# Must match the marker used in groq_service._build_messages for decoding.
# When the agent calls tools, we encode the tool call as an assistant message
# with this prefix so the LLM can replay its own reasoning on the next turn.
_TOOL_CALLS_MARKER = "__tool_calls__:"

# ── Tool result slimmer ───────────────────────────────────────────────────────
# Full Mongo documents can be 500+ tokens. We strip fields the LLM never needs
# so history re-plays are cheap. Only applied before DB storage — the live
# in-request result that the LLM sees this turn is always the full response.

_FIELDS_TO_DROP_FROM_ORDERS = {
    "_seed", "userId", "invoiceId", "_id", "payment_summary",
}
_PRODUCT_FIELDS_TO_KEEP = {"name", "price", "amount"}


def _slim_tool_result(tool_name: str, content_str: str) -> str:
    """
    Strip heavyweight fields from tool results before persisting.
    Returns the original string if parsing fails or tool is not handled.
 
    With the meta-tool architecture, the stored tool_name is "tool_invoke".
    The real tool name is tagged inside the result JSON as _invoked_tool
    by ToolInvokeTool.execute(). We read that tag to apply the right slimming.
 
    The _invoked_tool tag itself is stripped after use — it's internal only.
    """
    try:
        result = json.loads(content_str)
        if not result.get("success"):
            return content_str  # keep error messages intact
 
        # ── Resolve the real tool name ──────────────────────────────────────
        # In meta-tool mode: result["_invoked_tool"] = "get_order_details" etc.
        # In direct mode (legacy / fallback): tool_name is already the real name.
        real_tool_name = result.pop("_invoked_tool", None) or tool_name
 
        data = result.get("data", {})
 
        if real_tool_name == "get_order_details" and isinstance(data, dict):
            for f in _FIELDS_TO_DROP_FROM_ORDERS:
                data.pop(f, None)
            if "products" in data:
                data["products"] = [
                    {k: v for k, v in p.items() if k in _PRODUCT_FIELDS_TO_KEEP}
                    for p in data["products"]
                ]
            data.pop("status_history", None)
            if "delivery_date_change_request" in data:
                req = data["delivery_date_change_request"]
                data["delivery_date_change_request"] = {
                    "status":         req.get("status"),
                    "requested_date": req.get("requested_date"),
                }
            result["data"] = data
 
        elif real_tool_name == "get_order_history" and isinstance(data, dict):
            orders = data.get("orders", [])
            data["orders"] = [
                {
                    "order_id":           o.get("order_id"),
                    "status":             o.get("status"),
                    "estimated_delivery": o.get("estimated_delivery"),
                    "items":              o.get("items", [])[:3],
                }
                for o in orders
            ]
            result["data"] = data
 
        elif real_tool_name == "get_user_profile" and isinstance(data, dict):
            keep = {"name", "surname", "email", "loyaltyTier", "loyaltyPoints", "accountStatus"}
            result["data"] = {k: v for k, v in data.items() if k in keep}
        elif real_tool_name == "reorder_last_order" and isinstance(data, dict):
            keep = {"outcome", "items", "total_items", "order_total",
                    "ship_to", "estimated_delivery", "message"}
            result["data"] = {k: v for k, v in data.items() if k in keep}
 
        # tool_search and tool_invoke results are small — no slimming needed.
        # think results are tiny — no slimming needed.
 
        return json.dumps(result)
 
    except Exception:
        return content_str  # never crash — return original on any error
    
class ConversationStore:
    def __init__(self, db=None, session_factory=None):
        self._db              = db
        self._session_factory = session_factory

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_or_create(self, session_id: str, user_id: str) -> dict:
        if settings.db_tool_mode == "postgres":
            return await self._pg_get_or_create(session_id, user_id)
        return await self._mongo_get_or_create(session_id, user_id)

    async def append_turn(
        self,
        session_id:   str,
        user_message: str,
        bot_reply:    str,
        tool_calls:   list = [],
        tool_results: list = [],
    ) -> None:
        """
        Persist a full conversation turn including any intermediate tool calls.

        Stored as an ordered sequence of messages per turn:
          1. user message
          2. [if tools were called] assistant message encoding the tool call(s)
          3. [if tools were called] one tool-result message per tool call
          4. final assistant text reply

        This sequence is what groq_service._build_messages() needs to reconstruct
        proper Groq API message history on the next turn.
        """
        if settings.db_tool_mode == "postgres":
            return await self._pg_append_turn(
                session_id, user_message, bot_reply, tool_calls, tool_results
            )
        return await self._mongo_append_turn(
            session_id, user_message, bot_reply, tool_calls, tool_results
        )

    async def append_notification(
        self,
        session_id: str,
        message:    str,
        status:     str,
    ) -> None:
        if settings.db_tool_mode == "postgres":
            return await self._pg_append_notification(session_id, message, status)
        return await self._mongo_append_notification(session_id, message, status)

    async def close_session(self, session_id: str) -> None:
        if settings.db_tool_mode == "postgres":
            return await self._pg_close_session(session_id)
        return await self._mongo_close_session(session_id)

    async def get_history(self, user_id: str, limit: int = 5) -> list:
        if settings.db_tool_mode == "postgres":
            return await self._pg_get_history(user_id, limit)
        return await self._mongo_get_history(user_id, limit)

    # ── Mongo ─────────────────────────────────────────────────────────────────

    async def _mongo_get_or_create(self, session_id: str, user_id: str) -> dict:
        existing = await self._db.conversations.find_one({"session_id": session_id})
        if existing:
            return existing

        now = datetime.now(timezone.utc)
        doc = {
            "session_id":  session_id,
            "user_id":     ObjectId(user_id),
            "messages":    [],
            "status":      "active",
            "created_at":  now,
            "last_active": now,
        }
        result = await self._db.conversations.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def _mongo_append_turn(
        self,
        session_id:   str,
        user_message: str,
        bot_reply:    str,
        tool_calls:   list,
        tool_results: list,
    ) -> None:
        def _now():
            return datetime.now(timezone.utc)

        # Build the ordered message sequence for this turn.
        messages_to_add = [
            {"role": "user", "content": user_message, "timestamp": _now()}
        ]

        if tool_calls:
            # ── Step 2: Encode the assistant's tool call decision ──────────────
            tc_payload = [
                {
                    "id":        tc.id,
                    "name":      tc.tool_name,
                    "arguments": tc.arguments,
                }
                for tc in tool_calls
            ]

            messages_to_add.append({
                "role":      "assistant",
                "content":   f"{_TOOL_CALLS_MARKER}{json.dumps(tc_payload)}",
                "timestamp": _now(),
            })

            # ── Step 3: One tool-result message per tool call ──────────────────
            tc_id_to_name = {tc.id: tc.tool_name for tc in tool_calls}

            for tr in tool_results:
                messages_to_add.append({
                    "role":         "tool",
                    "content":      tr.content,
                    "tool_call_id": tr.tool_call_id,
                    "name":         tool_nm,
                    "timestamp":    _now(),
                })

        # ── Step 4: Final assistant text reply ────────────────────────────────
        messages_to_add.append({
            "role":      "assistant",
            "content":   bot_reply,
            "timestamp": _now(),
        })

        await self._db.conversations.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": messages_to_add}},
                "$set":  {"last_active": _now(), "status": "active"},
            }
        )
        
    async def _mongo_append_notification(self, session_id, message, status):
        now = datetime.now(timezone.utc)
        await self._db.conversations.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {
                    "role":      "notification",
                    "content":   message,
                    "status":    status,
                    "timestamp": now,
                }},
                "$set": {"last_active": now},
            }
        )

    async def _mongo_close_session(self, session_id: str):
        await self._db.conversations.update_one(
            {"session_id": session_id},
            {"$set": {"status": "closed"}}
        )

    async def _mongo_get_history(self, user_id: str, limit: int) -> list:
        try:
            uid = ObjectId(user_id) if not isinstance(user_id, ObjectId) else user_id
        except Exception:
            return []

        cursor = self._db.conversations.find(
            {"user_id": uid},
            {"messages": 1, "created_at": 1, "last_active": 1, "session_id": 1}
        ).sort("last_active", -1).limit(limit)

        conversations = []
        async for conv in cursor:
            conversations.append({
                "session_id":  conv["session_id"],
                "created_at":  conv["created_at"].isoformat(),
                "last_active": conv["last_active"].isoformat(),
                "messages": [
                    {
                        "role":         m["role"],
                        "content":      m.get("content", ""),  # FIX: use .get() to avoid KeyError on messages missing 'content'
                        "timestamp":    m["timestamp"].isoformat(),
                        "status":       m.get("status"),
                        # These are only present on tool messages — None for all others.
                        "tool_call_id": m.get("tool_call_id"),
                        "name":         m.get("name"),
                    }
                    for m in conv.get("messages", [])
                ]
            })
        return conversations

    # ── PG ────────────────────────────────────────────────────────────────────

    async def _pg_get_or_create(self, session_id: str, user_id: str) -> dict:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.session_id == session_id)
                .options(selectinload(Conversation.messages))
            )
            conv = result.scalar_one_or_none()
            if conv:
                return self._pg_conv_to_dict(conv)

            now  = datetime.now(timezone.utc)
            conv = Conversation(
                session_id  = session_id,
                user_id     = user_id,
                status      = "active",
                created_at  = now,
                last_active = now,
                messages    = [],
            )
            session.add(conv)
            await session.commit()

            result = await session.execute(
                select(Conversation)
                .where(Conversation.session_id == session_id)
                .options(selectinload(Conversation.messages))
            )
            conv = result.scalar_one()
            return self._pg_conv_to_dict(conv)

    async def _pg_append_turn(self, session_id, user_message, bot_reply, tool_calls, tool_results):
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:

            # Get current max sequence for this session
            seq_result = await session.execute(
                text("SELECT COALESCE(MAX(sequence), 0) FROM conversation_messages WHERE session_id = :sid"),
                {"sid": session_id}
            )
            seq = seq_result.scalar() + 1

            # 1. User message
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "user",
                content    = user_message,
                timestamp  = now,
                sequence   = seq,
            ))
            seq += 1

            if tool_calls:
                # 2. Assistant tool-call decision
                tc_payload = [{"id": tc.id, "name": tc.tool_name, "arguments": tc.arguments} for tc in tool_calls]
                session.add(ConversationMessage(
                    session_id = session_id,
                    role       = "assistant",
                    content    = f"{_TOOL_CALLS_MARKER}{json.dumps(tc_payload)}",
                    timestamp  = now,
                    sequence   = seq,
                ))
                seq += 1

                # 3. Tool results
                tc_id_to_name = {tc.id: tc.tool_name for tc in tool_calls}
                for tr in tool_results:
                    session.add(ConversationMessage(
                        session_id   = session_id,
                        role         = "tool",
                        content      = tr.content,
                        tool_call_id = tr.tool_call_id,
                        name         = tc_id_to_name.get(tr.tool_call_id, "unknown"),
                        timestamp    = now,
                        sequence     = seq,
                    ))
                    seq += 1

            # 4. Final assistant reply
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "assistant",
                content    = bot_reply,
                timestamp  = now,
                sequence   = seq,
            ))

            await session.execute(
                update(Conversation)
                .where(Conversation.session_id == session_id)
                .values(last_active=now, status="active")
            )
            await session.commit()

    async def _pg_append_notification(self, session_id, message, status):
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            seq_result = await session.execute(
                text("SELECT COALESCE(MAX(sequence), 0) FROM conversation_messages WHERE session_id = :sid"),
                {"sid": session_id}
            )
            seq = seq_result.scalar()+1

            session.add(ConversationMessage(
                session_id = session_id,
                role       = "notification",
                content    = message,
                name       = status,
                timestamp  = now,
                sequence   = seq,
            ))
            await session.execute(
                update(Conversation)
                .where(Conversation.session_id == session_id)
                .values(last_active=now)
            )
            await session.commit()

    async def _pg_close_session(self, session_id: str):
        async with self._session_factory() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.session_id == session_id)
                .values(status="closed")
            )
            await session.commit()

    async def _pg_get_history(self, user_id: str, limit: int) -> list:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .options(selectinload(Conversation.messages))
                .order_by(Conversation.last_active.desc())
                .limit(limit)
            )
            convs = result.scalars().all()
            return [self._pg_conv_to_dict(c) for c in convs]

    # ── Helper ────────────────────────────────────────────────────────────────

    def _pg_conv_to_dict(self, conv: Conversation) -> dict:
        return {
            "session_id":  conv.session_id,
            "created_at":  conv.created_at.isoformat(),
            "last_active": conv.last_active.isoformat(),
            "messages": [
                {
                    "role":         m.role,
                    "content":      m.content,
                    "timestamp":    m.timestamp.isoformat(),
                    "status":       m.name if m.role == "notification" else None,
                    "tool_call_id": m.tool_call_id,
                    "name":         m.name if m.role != "notification" else None,
                }
                for m in (conv.messages or [])
            ]
        }