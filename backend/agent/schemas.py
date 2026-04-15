# backend/agent/schemas.py

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class Role(str, Enum):
    user      = "user"
    assistant = "assistant"
    tool      = "tool"
    system    = "system"


class Message(BaseModel):
    role:         Role
    content:      str
    tool_call_id: str | None = None   # only for role=tool responses
    name:         str | None = None   # tool name, only for role=tool


class ToolCall(BaseModel):
    id:        str
    tool_name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Raw Groq tool result dict — stored so it can be replayed next turn."""
    tool_call_id: str
    content:      str   # JSON string of the tool's return value


class AgentResponse(BaseModel):
    message:       str
    tool_calls:    list[ToolCall]  = []
    tool_results:  list[ToolResult] = []
    was_escalated: bool = False
    error:         str | None = None


class ChatRequest(BaseModel):
    message:    str    = Field(..., min_length=1, max_length=2000)
    session_id: str    = Field(..., description="Unique conversation session ID")
    user_email: str | None = Field(None, description="Customer email if known")
    order_id:   str | None = Field(None, description="Order ID if customer provided one")


class ChatResponse(BaseModel):
    reply:         str
    session_id:    str
    was_escalated: bool = False
    # ── FIX: use timezone-aware UTC datetime ──────────────────────────────────
    # datetime.utcnow() produces a *naive* datetime with no tzinfo.
    # FastAPI serialises it as "2026-04-13T12:33:21" (no Z / +00:00).
    # JavaScript's Date constructor then interprets that as *local* time on
    # many runtimes, shifting the displayed time by the UTC offset.
    # datetime.now(timezone.utc) produces an *aware* datetime, serialised as
    # "2026-04-13T12:33:21+00:00", which JS always parses as UTC.
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )