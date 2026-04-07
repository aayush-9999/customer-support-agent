# backend/services/conversation_store.py

import json
import logging
from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from backend.core.config import get_settings
from backend.models.conversation import Conversation, ConversationMessage

logger   = logging.getLogger(__name__)
settings = get_settings()

# Must match the marker used in groq_service._build_messages for decoding.
# When the agent calls tools, we encode the tool call as an assistant message
# with this prefix so the LLM can replay its own reasoning on the next turn.
_TOOL_CALLS_MARKER = "__tool_calls__:"


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
        now = datetime.now(timezone.utc)

        # Build the ordered message sequence for this turn.
        messages_to_add = [
            {"role": "user", "content": user_message, "timestamp": now}
        ]

        if tool_calls:
            # ── Step 2: Encode the assistant's tool call decision ──────────────
            # groq_service._build_messages() will decode this back into a proper
            # Groq "assistant + tool_calls" message on the next turn.
            tc_payload = [
                {
                    "id":        tc.id,
                    "name":      tc.tool_name,
                    "arguments": tc.arguments,   # dict, not string
                }
                for tc in tool_calls
            ]
            messages_to_add.append({
                "role":      "assistant",
                "content":   f"{_TOOL_CALLS_MARKER}{json.dumps(tc_payload)}",
                "timestamp": now,
            })

            # ── Step 3: One tool-result message per tool call ──────────────────
            # Build a lookup so we can attach the tool name to each result.
            tc_id_to_name = {tc.id: tc.tool_name for tc in tool_calls}

            for tr in tool_results:
                messages_to_add.append({
                    "role":         "tool",
                    "content":      tr.content,
                    "tool_call_id": tr.tool_call_id,
                    "name":         tc_id_to_name.get(tr.tool_call_id, "unknown"),
                    "timestamp":    now,
                })

        # ── Step 4: Final assistant text reply ────────────────────────────────
        messages_to_add.append({
            "role":      "assistant",
            "content":   bot_reply,
            "timestamp": now,
        })

        await self._db.conversations.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": messages_to_add}},
                "$set":  {"last_active": now, "status": "active"},
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
                        "content":      m["content"],
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

    async def _pg_append_turn(
        self,
        session_id:   str,
        user_message: str,
        bot_reply:    str,
        tool_calls:   list,
        tool_results: list,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:

            # 1. User message
            session.add(ConversationMessage(
                session_id   = session_id,
                role         = "user",
                content      = user_message,
                timestamp    = now,
            ))

            if tool_calls:
                # 2. Assistant tool-call decision (encoded with marker)
                tc_payload = [
                    {
                        "id":        tc.id,
                        "name":      tc.tool_name,
                        "arguments": tc.arguments,
                    }
                    for tc in tool_calls
                ]
                session.add(ConversationMessage(
                    session_id = session_id,
                    role       = "assistant",
                    content    = f"{_TOOL_CALLS_MARKER}{json.dumps(tc_payload)}",
                    timestamp  = now,
                ))

                # 3. Tool result messages
                tc_id_to_name = {tc.id: tc.tool_name for tc in tool_calls}
                for tr in tool_results:
                    session.add(ConversationMessage(
                        session_id   = session_id,
                        role         = "tool",
                        content      = tr.content,
                        tool_call_id = tr.tool_call_id,
                        name         = tc_id_to_name.get(tr.tool_call_id, "unknown"),
                        timestamp    = now,
                    ))

            # 4. Final assistant text reply
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "assistant",
                content    = bot_reply,
                timestamp  = now,
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
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "notification",
                content    = message,
                # Reuse the `name` field to carry the status string for notifications.
                name       = status,
                timestamp  = now,
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
                    # For notifications, status was stored in the `name` field.
                    "status":       m.name if m.role == "notification" else None,
                    "tool_call_id": m.tool_call_id,
                    "name":         m.name if m.role != "notification" else None,
                }
                for m in (conv.messages or [])
            ]
        }