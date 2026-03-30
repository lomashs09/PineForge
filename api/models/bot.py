"""Bot model — represents a configured trading bot."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id"), nullable=False
    )
    script_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    lot_size: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    max_lot_size: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), default=Decimal("0.1"), server_default=text("0.1")
    )
    max_daily_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("5.00"), server_default=text("5.00")
    )
    max_open_positions: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default=text("60"))
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default=text("60"))
    lookback_bars: Mapped[int] = mapped_column(Integer, default=200, server_default=text("200"))
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        String(20), default="stopped", server_default=text("'stopped'")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="bots")
    broker_account = relationship("BrokerAccount", back_populates="bots")
    script = relationship("Script", back_populates="bots")
    logs = relationship("BotLog", back_populates="bot", cascade="all, delete-orphan")
    trades = relationship("BotTrade", back_populates="bot", cascade="all, delete-orphan")
