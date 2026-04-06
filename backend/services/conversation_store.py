# backend/services/conversation_store.py

import logging
from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class ConversationStore:
    """
    Handles reading and writing conversation history to MongoDB.

    Message schema in MongoDB `messages` array:
      Normal turn (always saved):
        { role: "user",      content: "..." }
        { role: "assistant", content: "...", tool_calls: [...] }

      Tool context turns (saved when tools were used — so order_ids etc.
      survive into the next turn's LLM context):
        { role: "tool_call",   tool_name: "...", tool_id: "...", arguments: {...} }
        { role: "tool_result", tool_id:   "...", content: "{...json...}" }

      Notification (admin approval/rejection banners):
        { role: "notification", content: "...", status: "approved"|"rejected" }
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def get_or_create(self, session_id: str, user_id: str) -> dict:
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

    async def append_turn(
        self,
        session_id:   str,
        user_message: str,
        bot_reply:    str,
        tool_calls:   list = [],   # list[ToolCall] from AgentResponse
        tool_results: list = [],   # list[dict] — the raw Groq tool result dicts
    ) -> None:
        """
        Persist one full turn to MongoDB.

        When tools were called we save the tool call + result rows BEFORE the
        assistant text reply. This means on the next turn, when we reload history
        and feed it back to the LLM, it can see the actual tool outputs (including
        order_ids, dates, etc.) — exactly as if this were a single long session.
        Without this, the model loses all tool data between turns and hallucinates.
        """
        now = datetime.now(timezone.utc)
        messages_to_add = []

        # 1. User message
        messages_to_add.append({
            "role":      "user",
            "content":   user_message,
            "timestamp": now,
        })

        # 2. Tool call + result pairs (only when tools were actually called)
        #    We zip tool_calls (ToolCall objects) with tool_results (Groq dicts)
        #    to keep them paired. If counts mismatch we just skip extras.
        for tc, tr in zip(tool_calls, tool_results):
            messages_to_add.append({
                "role":      "tool_call",
                "tool_id":   tc.id,
                "tool_name": tc.tool_name,
                "arguments": tc.arguments,
                "timestamp": now,
            })
            messages_to_add.append({
                "role":      "tool_result",
                "tool_id":   tr.tool_call_id,
                "content":   tr.content,
                "timestamp": now,
            })

        # 3. Final assistant text reply
        messages_to_add.append({
            "role":       "assistant",
            "content":    bot_reply,
            "timestamp":  now,
            "tool_calls": [t.tool_name for t in tool_calls],
        })

        await self._db.conversations.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": messages_to_add}},
                "$set":  {"last_active": now, "status": "active"},
            }
        )

    async def append_notification(
        self,
        session_id: str,
        message:    str,
        status:     str,  # "approved" | "rejected"
    ) -> None:
        """Persist an admin notification as a banner-type row (not a bubble)."""
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
        logger.info(f"Notification persisted — session={session_id} status={status}")

    async def close_session(self, session_id: str) -> None:
        await self._db.conversations.update_one(
            {"session_id": session_id},
            {"$set": {"status": "closed"}}
        )

    async def get_history(self, user_id: str, limit: int = 5) -> list:
        """
        Returns conversations for the sidebar.
        Only user/assistant/notification rows go to the frontend — tool rows
        are internal LLM context and not rendered in the UI.
        """
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

    async def get_full_history_for_llm(self, session_id: str) -> list[dict]:
        """
        Returns ALL message rows including tool_call / tool_result — used
        by routes.py to rebuild the full LLM context for the next turn.
        """
        conv = await self._db.conversations.find_one({"session_id": session_id})
        if not conv:
            return []
        return conv.get("messages", [])