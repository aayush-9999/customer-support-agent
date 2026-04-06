# backend/api/routes.py

import logging
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import DESCENDING

from backend.agent.loop import run_agent
from backend.agent.schemas import ChatRequest, ChatResponse, Message, Role
from backend.api.dependencies import get_groq, get_policy, get_current_user, get_conversations
from backend.policies.file_store import FilePolicyStore
from backend.services.llm_base import LLMBase
from backend.services.conversation_store import ConversationStore
from backend.database import get_db
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatInput(BaseModel):
    message:    str = Field(..., min_length=1, max_length=2000)
    session_id: str
    order_id:   str | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body:          ChatInput,
    current_user:  dict                 = Depends(get_current_user),
    llm:           LLMBase              = Depends(get_groq),
    policy:        FilePolicyStore      = Depends(get_policy),
    conversations: ConversationStore    = Depends(get_conversations),
    db:            AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        await conversations.get_or_create(
            session_id = body.session_id,
            user_id    = str(current_user["_id"]),
        )

        request = ChatRequest(
            message    = body.message,
            session_id = body.session_id,
            user_email = current_user.get("email"),
            order_id   = body.order_id,
        )

        # ── Rebuild full LLM history including tool rows ───────────────────────
        # Core fix for the "lost order_id" bug. We load ALL stored rows
        # (user, assistant, tool_call, tool_result, notification) and convert
        # them into Message objects the LLM can reason over. Tool result rows
        # carry the actual order_ids returned by get_order_history — without
        # them, the model has no way to know the real ID and hallucinates.
        raw_history = await conversations.get_full_history_for_llm(body.session_id)
        history: list[Message] = _rebuild_llm_history(raw_history)

        response = await run_agent(
            request      = request,
            llm          = llm,
            policy_store = policy,
            history      = history,
        )

        # Backfill session_id on the pending_request so WS can find it
        if any(tc.tool_name == "change_delivery_date" for tc in response.tool_calls):
            await db.pending_requests.find_one_and_update(
                {
                    "user_id":    ObjectId(str(current_user["_id"])),
                    "status":     "pending",
                    "session_id": None,
                },
                {"$set": {"session_id": body.session_id}},
                sort=[("created_at", DESCENDING)],
            )

        # Persist turn with full tool context so next turn has the data
        await conversations.append_turn(
            session_id   = body.session_id,
            user_message = body.message,
            bot_reply    = response.message,
            tool_calls   = response.tool_calls,
            tool_results = response.tool_results,
        )

        return ChatResponse(
            reply         = response.message,
            session_id    = body.session_id,
            was_escalated = response.was_escalated,
        )

    except Exception as e:
        logger.exception(f"Chat failed — session={body.session_id}")
        raise HTTPException(status_code=500, detail="Something went wrong.")


def _rebuild_llm_history(raw_messages: list[dict]) -> list[Message]:
    """
    Convert stored MongoDB rows back into Message objects for the LLM.

    Handles four row types:
      user        → Role.user
      assistant   → Role.assistant
      tool_call   → encoded as Role.assistant with __tool_calls__ marker
                    (groq_service._build_messages decodes this back to
                     the proper Groq tool_calls structure)
      tool_result → Role.tool with tool_call_id
      notification → skipped (UI banner, not LLM context)
    """
    result = []
    i = 0

    while i < len(raw_messages):
        m = raw_messages[i]
        role = m.get("role")

        if role == "user":
            result.append(Message(role=Role.user, content=m["content"]))
            i += 1

        elif role == "assistant":
            result.append(Message(role=Role.assistant, content=m["content"]))
            i += 1

        elif role == "tool_call":
            # Collect all consecutive tool_call rows then all consecutive
            # tool_result rows — they were stored in interleaved pairs.
            tool_call_rows   = []
            tool_result_rows = []

            j = i
            while j < len(raw_messages) and raw_messages[j]["role"] == "tool_call":
                tool_call_rows.append(raw_messages[j])
                j += 1
            while j < len(raw_messages) and raw_messages[j]["role"] == "tool_result":
                tool_result_rows.append(raw_messages[j])
                j += 1

            # Encode as a special assistant message that groq_service detects
            payload = json.dumps([
                {
                    "id":        tc["tool_id"],
                    "name":      tc["tool_name"],
                    "arguments": tc.get("arguments", {}),
                }
                for tc in tool_call_rows
            ])
            result.append(Message(
                role    = Role.assistant,
                content = f"__tool_calls__:{payload}",
            ))

            for tr in tool_result_rows:
                result.append(Message(
                    role         = Role.tool,
                    content      = tr["content"],
                    tool_call_id = tr["tool_id"],
                ))

            i = j

        else:
            # notification, unknown legacy rows → skip
            i += 1

    return result


@router.get("/conversations")
async def get_conversations_history(
    current_user:  dict              = Depends(get_current_user),
    conversations: ConversationStore = Depends(get_conversations),
):
    history = await conversations.get_history(
        user_id = str(current_user["_id"]),
        limit   = 5,
    )
    return {"conversations": history}


@router.post("/conversations/close")
async def close_conversation(
    body:          dict              = {},
    current_user:  dict              = Depends(get_current_user),
    conversations: ConversationStore = Depends(get_conversations),
):
    session_id = body.get("session_id")
    if session_id:
        await conversations.close_session(session_id)
    return {"status": "closed"}


@router.get("/session/new")
async def new_session():
    return {"session_id": str(uuid.uuid4())}


@router.get("/health/deep")
async def deep_health(
    llm:    LLMBase         = Depends(get_groq),
    policy: FilePolicyStore = Depends(get_policy),
):
    return {
        "status":       "ok",
        "llm":          llm.__class__.__name__,
        "policy_store": policy.__class__.__name__,
    }