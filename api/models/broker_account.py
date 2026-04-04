"""Broker account model — one row per Exness MT5 account."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    metaapi_account_id: Mapped[str] = mapped_column(String(100), nullable=True, default="")
    mt5_login: Mapped[str] = mapped_column(String(50), nullable=False)
    mt5_password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mt5_server: Mapped[str] = mapped_column(String(100), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(50), default="exness", server_default=text("'exness'"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="broker_accounts")
    bots = relationship("Bot", back_populates="broker_account")
