# backend/models/user.py

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class User(Base):
    __tablename__ = "users"

    id:             Mapped[str]      = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name:           Mapped[str]      = mapped_column(String, nullable=False)
    surname:        Mapped[str]      = mapped_column(String, nullable=False)
    email:          Mapped[str]      = mapped_column(String, unique=True, nullable=False, index=True)
    password:       Mapped[str]      = mapped_column(String, nullable=False)
    phone:          Mapped[str|None] = mapped_column(String, nullable=True)
    role:           Mapped[str]      = mapped_column(String, default="customer")
    is_active:      Mapped[bool]     = mapped_column(Boolean, default=True)
    account_status: Mapped[str]      = mapped_column(String, default="active")
    loyalty_tier:   Mapped[str]      = mapped_column(String, default="Bronze")
    loyalty_points: Mapped[int]      = mapped_column(Integer, default=50)
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))