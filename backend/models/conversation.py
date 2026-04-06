# backend/models/conversation.py

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON, Text
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

    id:          Mapped[str]      = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id:  Mapped[str]      = mapped_column(String, ForeignKey("conversations.session_id"), nullable=False, index=True)
    role:        Mapped[str]      = mapped_column(String, nullable=False)
    content:     Mapped[str]      = mapped_column(Text, nullable=False)
    tool_calls:  Mapped[list]     = mapped_column(JSON, default=list)
    timestamp:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")