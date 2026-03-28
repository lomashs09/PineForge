"""Bot trade model — records every trade executed by a bot."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class BotTrade(Base):
    __tablename__ = "bot_trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    lot_size: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 5), nullable=False)
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 5), nullable=True)
    pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 5), nullable=True)
    signal: Mapped[str] = mapped_column(String(20), nullable=False)
    order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    bot = relationship("Bot", back_populates="trades")
