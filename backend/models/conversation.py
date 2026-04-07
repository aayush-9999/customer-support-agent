# backend/models/conversation.py

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    session_id:  Mapped[str]      = mapped_column(String, primary_key=True)
    user_id:     Mapped[str]      = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    status:      Mapped[str]      = mapped_column(String, default="active")
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        order_by="ConversationMessage.timestamp",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id:           Mapped[str]        = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id:   Mapped[str]        = mapped_column(String, ForeignKey("conversations.session_id"), nullable=False, index=True)
    role:         Mapped[str]        = mapped_column(String, nullable=False)
    content:      Mapped[str]        = mapped_column(Text, nullable=False)
    timestamp:    Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ── Tool call tracking ────────────────────────────────────────────────────
    # These are only populated for role="tool" messages (tool result rows).
    # For all other roles both columns are NULL.
    #
    # tool_call_id: the Groq tool call ID that generated this result.
    #               Used by groq_service._build_messages() to reconstruct
    #               the tool → result linkage for the LLM on the next turn.
    #
    # name:         the tool function name (e.g. "get_order_history").
    #               Also used for notifications: stores the status string
    #               ("approved" / "rejected") so _pg_conv_to_dict can
    #               surface it without a separate column.
    #
    # NOTE: If you are running PostgreSQL and upgrading an existing DB,
    # run this migration:
    #   ALTER TABLE conversation_messages ADD COLUMN tool_call_id VARCHAR;
    #   ALTER TABLE conversation_messages ADD COLUMN name VARCHAR;
    # New databases created via create_all will have these columns automatically.
    tool_call_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    name:         Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")