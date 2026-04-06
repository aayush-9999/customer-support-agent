# backend/services/conversation_store.py

import logging
import uuid
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


class ConversationStore:
    def __init__(self, db, session_factory=None):
        self._db              = db               # Mongo db or None
        self._session_factory = session_factory  # PG session factory or None

    # ── Public ────────────────────────────────────────────────────────────────

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
        tool_results: list = [],   # ← ADD
    ) -> None:
        if settings.db_tool_mode == "postgres":
            return await self._pg_append_turn(session_id, user_message, bot_reply, tool_calls)
        return await self._mongo_append_turn(session_id, user_message, bot_reply, tool_calls, tool_results)
    
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

    async def _mongo_append_turn(self, session_id, user_message, bot_reply, tool_calls, tool_results=None):
        import json
        now = datetime.now(timezone.utc)
        messages_to_add = []

        # 1. User message
        messages_to_add.append({
            "role":      "user",
            "content":   user_message,
            "timestamp": now,
        })

        # 2. If tools were called, save the encoded tool_calls assistant row
        #    so _build_messages() can reconstruct it on the next turn
        if tool_calls:
            encoded_payload = json.dumps([
                {"id": tc.id, "name": tc.tool_name, "arguments": tc.arguments}
                for tc in tool_calls
            ])
            messages_to_add.append({
                "role":      "assistant",
                "content":   f"__tool_calls__:{encoded_payload}",
                "timestamp": now,
            })

        # 3. Save each tool result row (so the model sees what tools returned)
        if tool_results:
            for tr in tool_results:
                messages_to_add.append({
                    "role":         "tool",
                    "content":      tr.content,
                    "tool_call_id": tr.tool_call_id,
                    "timestamp":    now,
                })

        # 4. Final assistant reply (the clean text shown to user)
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
            ui_messages = []
            for m in conv.get("messages", []):
                role = m["role"]
                if role in ("user", "assistant"):
                    ui_messages.append({
                        "role":      role,
                        "content":   m["content"],
                        "timestamp": m["timestamp"].isoformat(),
                    })
                elif role == "notification":
                    ui_messages.append({
                        "role":      "notification",
                        "content":   m["content"],
                        "status":    m.get("status", "approved"),
                        "timestamp": m["timestamp"].isoformat(),
                    })
                # tool_call / tool_result rows: skipped — UI doesn't render them

            conversations.append({
                "session_id":  conv["session_id"],
                "created_at":  conv["created_at"].isoformat(),
                "last_active": conv["last_active"].isoformat(),
                "messages":    ui_messages,
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
                messages    = [],   # ← set empty list directly, no lazy load
            )
            session.add(conv)
            await session.commit()

            # ── re-fetch with eager load instead of refresh ──
            result = await session.execute(
                select(Conversation)
                .where(Conversation.session_id == session_id)
                .options(selectinload(Conversation.messages))
            )
            conv = result.scalar_one()
            return self._pg_conv_to_dict(conv)

    async def _pg_append_turn(self, session_id, user_message, bot_reply, tool_calls):
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "user",
                content    = user_message,
                tool_calls = [],
                timestamp  = now,
            ))
            session.add(ConversationMessage(
                session_id = session_id,
                role       = "assistant",
                content    = bot_reply,
                tool_calls = [t.tool_name for t in tool_calls],
                timestamp  = now,
            ))
            await session.execute(
                update(Conversation)
                .where(Conversation.session_id == session_id)
                .values(last_active=now, status="active")
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
                    "role":      m.role,
                    "content":   m.content,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in (conv.messages or [])
            ]
        }
