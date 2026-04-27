"""MetaAPI streaming trade listener (Phase 3 reliability work).

Replaces the parsed-print path in BotPrintCapture for trade detection.
Hooks the broker's deal-added event directly so a trade lands in our DB
the moment the broker confirms it — no regex on log output, no buffering,
no race with bridge reconnects, no fragile coupling to log message format.

Each running bot gets one BotTradeListener attached to its MetaAPI RPC
connection. The listener filters by the bot's magic number, so events
for other bots on the same account or manual user trades are ignored.

Idempotent — safe to run alongside the parsed-print path during the
migration window. Inserts use ON CONFLICT DO NOTHING on (bot_id, order_id).
Closes use a guarded UPDATE that no-ops if the row is already terminal.
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

# Predicate must match the partial-index WHERE clause from migration
# c1d2e3f4a5b6 exactly, otherwise PG raises "no unique or exclusion
# constraint matching the ON CONFLICT specification".
_PARTIAL_INDEX_WHERE = text(
    "order_id IS NOT NULL "
    "AND order_id NOT LIKE 'close-all%' "
    "AND order_id NOT LIKE 'dry-run%'"
)

try:
    from metaapi_cloud_sdk import SynchronizationListener
except Exception:  # pragma: no cover — dev environments without the SDK
    class SynchronizationListener:  # type: ignore
        async def on_deal_added(self, *args, **kwargs):
            pass

from ..models.bot_trade import BotTrade
from ..utils import metrics

logger = logging.getLogger(__name__)


class BotTradeListener(SynchronizationListener):
    """Listens for broker deal events and writes them to bot_trades.

    Why subclass SynchronizationListener: MetaAPI invokes the listener's
    async hooks directly inside its own event-loop. We must not raise out
    of those hooks (it'd kill the listener) and we must not do any heavy
    blocking work — DB inserts at ~1-5ms are fine; anything slower would
    need an asyncio.Queue between this and a worker task.
    """

    def __init__(
        self,
        bot_id: uuid.UUID,
        broker_account_id: uuid.UUID,
        magic_number: int,
        session_factory: async_sessionmaker,
    ):
        super().__init__()
        self.bot_id = bot_id
        self.broker_account_id = broker_account_id
        self.magic_number = int(magic_number) if magic_number else 0
        self.session_factory = session_factory

    # MetaAPI hook — must be async, must never raise.
    async def on_deal_added(self, instance_index, deal):  # noqa: D401
        try:
            await self._handle_deal(deal)
        except Exception:
            logger.exception(
                "BotTradeListener.on_deal_added failed bot_id=%s deal_id=%s",
                self.bot_id,
                deal.get("id") if isinstance(deal, dict) else None,
            )
            metrics.count(
                "bot.trade.listener_error",
                1,
                attributes={"hook": "on_deal_added"},
            )

    async def _handle_deal(self, deal: dict) -> None:
        if not isinstance(deal, dict):
            return

        # 1) Magic filter — ignore other bots and manual trades on the
        # same account. Magic is set by our executor at order placement.
        deal_magic = deal.get("magic")
        if deal_magic is not None:
            try:
                if int(deal_magic) != self.magic_number:
                    return
            except (TypeError, ValueError):
                return

        position_id = deal.get("positionId")
        entry = (deal.get("entryType") or "").upper()
        if not position_id or not entry:
            return
        position_id = str(position_id)

        if entry == "DEAL_ENTRY_INOUT":
            # Instant-fill close: a single deal that opens and closes
            # — record both, ON CONFLICT keeps the open idempotent if
            # we'd already seen it.
            await self._record_open(deal, position_id)
            await self._record_close(deal, position_id)
        elif "OUT" in entry:
            await self._record_close(deal, position_id)
        elif "IN" in entry:
            await self._record_open(deal, position_id)

    async def _record_open(self, deal: dict, position_id: str) -> None:
        deal_type = (deal.get("type") or "").upper()
        if "BUY" in deal_type:
            direction, signal = "long", "entry_long"
        elif "SELL" in deal_type:
            direction, signal = "short", "entry_short"
        else:
            logger.debug(
                "Skipping open with unknown deal type: bot_id=%s type=%r",
                self.bot_id, deal_type,
            )
            return

        opened_at = _coerce_datetime(deal.get("time")) or datetime.now(timezone.utc)
        entry_price = _decimal_or(deal.get("price"), Decimal("0"))
        volume = _decimal_or(deal.get("volume"), Decimal("0"))
        symbol = (deal.get("symbol") or "")[:20]

        try:
            async with self.session_factory() as session:
                stmt = (
                    pg_insert(BotTrade.__table__)
                    .values(
                        bot_id=self.bot_id,
                        broker_account_id=self.broker_account_id,
                        direction=direction,
                        symbol=symbol,
                        lot_size=volume,
                        entry_price=entry_price,
                        signal=signal,
                        order_id=position_id,
                        opened_at=opened_at,
                        lifecycle_state="open",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["bot_id", "order_id"],
                        index_where=_PARTIAL_INDEX_WHERE,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception:
            logger.exception(
                "Listener open insert failed bot_id=%s order_id=%s",
                self.bot_id, position_id,
            )
            metrics.count(
                "bot.trade.listener_error",
                1,
                attributes={"hook": "record_open"},
            )
            return

        logger.info(
            "Streaming open: bot_id=%s order_id=%s direction=%s symbol=%s price=%s vol=%s",
            self.bot_id, position_id, direction, symbol, entry_price, volume,
        )
        metrics.count(
            "bot.trade.listener_open",
            1,
            attributes={"direction": direction, "symbol": symbol},
        )

    async def _record_close(self, deal: dict, position_id: str) -> None:
        closed_at = _coerce_datetime(deal.get("time")) or datetime.now(timezone.utc)
        exit_price = _decimal_or(deal.get("price"), None)
        pnl = _decimal_or(deal.get("profit"), None)
        deal_type = (deal.get("type") or "").upper()

        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(BotTrade).where(
                        BotTrade.bot_id == self.bot_id,
                        BotTrade.order_id == position_id,
                    )
                )
                row = result.scalar_one_or_none()

                if row is None:
                    # Race: close arrived before open, or open was lost.
                    # Synthesise a closed row so the trade isn't dropped.
                    # Direction is inverted from the close-deal type:
                    # a SELL-OUT means we were long, a BUY-OUT means short.
                    direction = "long" if "SELL" in deal_type else "short"
                    volume = _decimal_or(deal.get("volume"), Decimal("0"))
                    logger.warning(
                        "Streaming close without open: bot_id=%s order_id=%s direction=%s",
                        self.bot_id, position_id, direction,
                    )
                    stmt = (
                        pg_insert(BotTrade.__table__)
                        .values(
                            bot_id=self.bot_id,
                            broker_account_id=self.broker_account_id,
                            direction=direction,
                            symbol=(deal.get("symbol") or "")[:20],
                            lot_size=volume,
                            entry_price=Decimal("0"),
                            exit_price=exit_price,
                            pnl=pnl,
                            signal=f"entry_{direction}",
                            order_id=position_id,
                            opened_at=closed_at,
                            closed_at=closed_at,
                            lifecycle_state="closed",
                        )
                        .on_conflict_do_nothing(
                        index_elements=["bot_id", "order_id"],
                        index_where=_PARTIAL_INDEX_WHERE,
                    )
                    )
                    await session.execute(stmt)
                    await session.commit()
                    metrics.count(
                        "bot.trade.listener_close",
                        1,
                        attributes={"orphan_open": True},
                    )
                    return

                if row.lifecycle_state in ("closed", "reconciled_external"):
                    return  # Already terminal — idempotent no-op

                row.exit_price = exit_price
                row.pnl = pnl
                row.closed_at = closed_at
                row.lifecycle_state = "closed"
                await session.commit()
        except Exception:
            logger.exception(
                "Listener close update failed bot_id=%s order_id=%s",
                self.bot_id, position_id,
            )
            metrics.count(
                "bot.trade.listener_error",
                1,
                attributes={"hook": "record_close"},
            )
            return

        logger.info(
            "Streaming close: bot_id=%s order_id=%s pnl=%s exit_price=%s",
            self.bot_id, position_id, pnl, exit_price,
        )
        metrics.count(
            "bot.trade.listener_close",
            1,
            attributes={"orphan_open": False},
        )


def _coerce_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


def _decimal_or(value, default):
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default
