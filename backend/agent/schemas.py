# backend/agent/schemas.py

from datetime import datetime
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
    tool_results:  list[ToolResult] = []   # ← NEW: parallel to tool_calls
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
    timestamp:     datetime = Field(default_factory=datetime.utcnow)