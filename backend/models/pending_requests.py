# backend/models/pending_requests.py

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, Date
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class PendingRequest(Base):
    __tablename__ = "pending_requests"

    id:              Mapped[str]           = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type:            Mapped[str]           = mapped_column(String, nullable=False)
    status:          Mapped[str]           = mapped_column(String, default="pending")
    order_id:        Mapped[str]           = mapped_column(String, nullable=False)
    user_id:         Mapped[str]           = mapped_column(String, nullable=False)
    session_id:      Mapped[str | None]    = mapped_column(String, nullable=True)
    created_at:      Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by:     Mapped[str|None]      = mapped_column(String, nullable=True)
    resolution_note: Mapped[str|None]      = mapped_column(Text, nullable=True)

    # date_change fields
    requested_date:  Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_date:    Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    # address_change fields
    requested_address: Mapped[str|None] = mapped_column(String, nullable=True)
    requested_city:    Mapped[str|None] = mapped_column(String, nullable=True)
    requested_state:   Mapped[str|None] = mapped_column(String, nullable=True)
    requested_pincode: Mapped[str|None] = mapped_column(String, nullable=True)
    current_address:   Mapped[str|None] = mapped_column(String, nullable=True)
    current_city:      Mapped[str|None] = mapped_column(String, nullable=True)
    current_state:     Mapped[str|None] = mapped_column(String, nullable=True)
    current_pincode:   Mapped[str|None] = mapped_column(String, nullable=True)

    # cancellation / return fields
    reason:                     Mapped[str|None] = mapped_column(Text, nullable=True)
    items:                      Mapped[str|None] = mapped_column(Text, nullable=True)   # JSON string
    refund_method:              Mapped[str|None] = mapped_column(String, nullable=True)
    return_shipping_covered_by: Mapped[str|None] = mapped_column(String, nullable=True) 