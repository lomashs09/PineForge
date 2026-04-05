"""Background task that periodically deletes old bot logs and trades."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models.bot_log import BotLog
from ..models.bot_trade import BotTrade

logger = logging.getLogger(__name__)

LOG_RETENTION_DAYS = 30
TRADE_RETENTION_DAYS = 90
CLEANUP_INTERVAL_HOURS = 24


async def log_cleanup_loop(session_factory: async_sessionmaker):
    """Run forever, deleting old logs/trades once per day."""
    while True:
        try:
            await _run_cleanup(session_factory)
        except Exception as e:
            logger.error("Log cleanup failed: %s", e)
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)


async def _run_cleanup(session_factory: async_sessionmaker):
    """Delete bot_logs older than 30 days and bot_trades older than 90 days."""
    now = datetime.now(timezone.utc)
    log_cutoff = now - timedelta(days=LOG_RETENTION_DAYS)
    trade_cutoff = now - timedelta(days=TRADE_RETENTION_DAYS)

    async with session_factory() as db:
        result = await db.execute(
            delete(BotLog).where(BotLog.created_at < log_cutoff)
        )
        logs_deleted = result.rowcount

        result = await db.execute(
            delete(BotTrade).where(BotTrade.opened_at < trade_cutoff)
        )
        trades_deleted = result.rowcount

        await db.commit()

    if logs_deleted or trades_deleted:
        logger.info("Log cleanup: deleted %d logs (>%dd) and %d trades (>%dd)",
                     logs_deleted, LOG_RETENTION_DAYS, trades_deleted, TRADE_RETENTION_DAYS)
