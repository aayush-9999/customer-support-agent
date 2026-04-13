# backend/api/routes.py

import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.agent.loop import run_agent
from backend.agent.schemas import ChatRequest, ChatResponse, Message, Role
from backend.api.dependencies import (
    get_current_user,
    get_groq,
    get_policy,
    get_conversations,
    get_tools,
)
from backend.policies.file_store import FilePolicyStore
from backend.services.llm_base import LLMBase
from backend.services.conversation_store import ConversationStore
from backend.tools.base import BaseTool
from backend.database import get_db
from backend.database_pg import get_pg_session
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

settings = get_settings()
logger   = logging.getLogger(__name__)
router   = APIRouter()


class ChatInput(BaseModel):
    """What the frontend sends — no email needed, comes from JWT."""
    message:    str       = Field(..., min_length=1, max_length=2000)
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
    pg_session:    AsyncSession         = Depends(get_pg_session),   # always injected; None when mongo mode
    tools:         list[BaseTool]       = Depends(get_tools),
):
    try:
        conv = await conversations.get_or_create(
            session_id = body.session_id,
            user_id    = str(current_user["_id"]),
        )

        request = ChatRequest(
            message    = body.message,
            session_id = body.session_id,
            user_email = current_user.get("email"),
            order_id   = body.order_id,
        )

        # ── Reconstruct full history including tool messages ──────────────────
        #
        # Each turn in the DB is stored as an ordered sequence:
        #   1. {role: "user",      content: "..."}
        #   2. {role: "assistant", content: "__tool_calls__:[...]"}  ← tool decision
        #   3. {role: "tool",      content: "...", tool_call_id: "...", name: "..."}
        #   4. {role: "assistant", content: "final reply text"}
        #
        # Roles we skip:
        #   "notification" — admin push messages, not part of LLM conversation
        #
        history: list[Message] = []
        for m in conv.get("messages", []):
            role_str = m.get("role", "")

            if role_str == "notification":
                continue

            try:
                role = Role(role_str)
            except ValueError:
                logger.warning(f"Unknown message role in history: '{role_str}' — skipping")
                continue

            history.append(Message(
                role         = role,
                content      = m["content"],
                tool_call_id = m.get("tool_call_id"),
                name         = m.get("name"),
            ))

        response = await run_agent(
            request      = request,
            llm          = llm,
            policy_store = policy,
            tools        = tools,
            history      = history,
        )
        
       # ── Link session_id after agent returns ──────────────────────────────────
        PENDING_TOOLS = {
            "change_delivery_date",
            "change_delivery_address", 
            "initiate_return",
            "report_missing_item",
            "cancel_order",
        }

        pending_tool_called = any(tc.tool_name in PENDING_TOOLS for tc in response.tool_calls)
        escalation_called   = any(tc.tool_name == "escalate_to_human" for tc in response.tool_calls)

        if pending_tool_called:
            if settings.db_tool_mode == "postgres" and pg_session is not None:
                await pg_session.execute(
                    text("""
                        UPDATE pending_requests
                        SET session_id = :session_id
                        WHERE id = (
                            SELECT id FROM pending_requests
                            WHERE user_id    = :user_id
                            AND status     = 'pending'
                            AND session_id IS NULL
                            AND type       IN ('date_change', 'address_change',
                                                'return_request', 'missing_item',
                                                'cancellation_request')
                            ORDER BY created_at DESC
                            LIMIT 1
                        )
                    """),
                    {
                        "session_id": body.session_id,
                        "user_id":    str(current_user["_id"]),
                    }
                )
                await pg_session.commit()

            elif settings.db_tool_mode == "mongo" and db is not None:
                from pymongo import DESCENDING
                await db.pending_requests.find_one_and_update(
                    {
                        "user_id":    ObjectId(str(current_user["_id"])),
                        "status":     "pending",
                        "session_id": None,
                        "type":       {"$in": [
                            "date_change", "address_change",
                            "return_request", "missing_item",
                            "cancellation_request",
                        ]},
                    },
                    {"$set": {"session_id": body.session_id}},
                    sort=[("created_at", DESCENDING)],
                )

        if escalation_called:
            if settings.db_tool_mode == "postgres" and pg_session is not None:
                await pg_session.execute(
                    text("""
                        UPDATE escalations
                        SET session_id = :session_id
                        WHERE id = (
                            SELECT id FROM escalations
                            WHERE user_id    = :user_id
                            AND status     = 'open'
                            AND session_id IS NULL
                            ORDER BY created_at DESC
                            LIMIT 1
                        )
                    """),
                    {
                        "session_id": body.session_id,
                        "user_id":    str(current_user["_id"]),
                    }
                )
                await pg_session.commit()

            elif settings.db_tool_mode == "mongo" and db is not None:
                from pymongo import DESCENDING
                await db.escalations.find_one_and_update(
                    {
                        "user_id":    ObjectId(str(current_user["_id"])),
                        "status":     "open",
                        "session_id": None,
                    },
                    {"$set": {"session_id": body.session_id}},
                    sort=[("created_at", DESCENDING)],
                )
        # ── Persist the full turn ─────────────────────────────────────────────
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


@router.get("/conversations")
async def get_conversations_history(
    current_user:  dict              = Depends(get_current_user),
    conversations: ConversationStore = Depends(get_conversations),
):
    """Returns last 5 conversations for the logged-in user."""
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
    """Called when user logs out to mark session as closed."""
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
