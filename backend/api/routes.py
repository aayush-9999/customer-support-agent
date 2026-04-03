# backend/api/routes.py

import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import DESCENDING

from backend.agent.loop import run_agent
from backend.agent.schemas import ChatRequest, ChatResponse
from backend.api.dependencies import get_groq, get_policy, get_current_user
from backend.policies.file_store import FilePolicyStore
from backend.services.llm_base import LLMBase
from backend.services.conversation_store import ConversationStore
from backend.api.dependencies import get_conversations
from backend.agent.schemas import Message, Role
from backend.database import get_db                          # ← add this
from motor.motor_asyncio import AsyncIOMotorDatabase         # ← add this too
from bson import ObjectId

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatInput(BaseModel):
    """What the frontend sends — no email needed, comes from JWT."""
    message:    str = Field(..., min_length=1, max_length=2000)
    session_id: str
    order_id:   str | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body:          ChatInput,
    current_user:  dict              = Depends(get_current_user),
    llm:           LLMBase           = Depends(get_groq),
    policy:        FilePolicyStore   = Depends(get_policy),
    conversations: ConversationStore = Depends(get_conversations),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        # Ensure conversation document exists for this session
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

        conv = await conversations.get_or_create(
            session_id = body.session_id,
            user_id    = str(current_user["_id"]),
        )

        # Convert stored dicts to Message objects the agent understands
        history: list[Message] = [
            Message(role=Role(m["role"]), content=m["content"])
            for m in conv.get("messages", [])
            if m["role"] in ("user", "assistant")  # skip any sentinel rows
        ]

        response = await run_agent(
            request      = request,
            llm          = llm,
            policy_store = policy,
            history      = history,
        )

        if any(tc.tool_name == "change_delivery_date" for tc in response.tool_calls):
            await db.pending_requests.find_one_and_update(
                {
                    "user_id": ObjectId(str(current_user["_id"])),
                    "status": "pending",
                    "session_id": None,
                },
                {"$set": {"session_id": body.session_id}},
                sort=[("created_at", DESCENDING)],
            )


        # Save turn to conversation history
        await conversations.append_turn(
            session_id   = body.session_id,
            user_message = body.message,
            bot_reply    = response.message,
            tool_calls   = response.tool_calls,
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
    """
    Returns last 5 conversations for the logged in user.
    Called when frontend loads after login.
    """
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
    llm:    LLMBase       = Depends(get_groq),
    policy: FilePolicyStore = Depends(get_policy),
):
    return {
        "status":       "ok",
        "llm":          llm.__class__.__name__,
        "policy_store": policy.__class__.__name__,
    }