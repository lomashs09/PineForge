"""Bot service — CRUD business logic and stats aggregation."""

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.bot import Bot
from ..models.bot_trade import BotTrade
from ..models.broker_account import BrokerAccount
from ..models.script import Script
from ..models.user import User


async def validate_bot_create(
    db: AsyncSession,
    user: User,
    broker_account_id: uuid.UUID,
    script_id: uuid.UUID,
) -> Optional[str]:
    """Validate bot creation constraints. Returns error string or None."""
    # Check bot limit
    result = await db.execute(
        select(func.count(Bot.id)).where(Bot.user_id == user.id)
    )
    bot_count = result.scalar()
    if bot_count >= user.max_bots:
        return f"Bot limit reached ({user.max_bots}). Delete a bot or upgrade your plan."

    # Check broker account belongs to user
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.id == broker_account_id,
            BrokerAccount.user_id == user.id,
            BrokerAccount.is_active == True,
        )
    )
    if result.scalar_one_or_none() is None:
        return "Broker account not found or not owned by you"

    # Check script is accessible
    result = await db.execute(
        select(Script).where(Script.id == script_id)
    )
    script = result.scalar_one_or_none()
    if script is None:
        return "Script not found"
    if not script.is_system and script.user_id != user.id:
        return "Script not accessible"

    return None


async def get_bot_stats(db: AsyncSession, bot_id: uuid.UUID) -> dict:
    """Aggregate trade statistics for a bot."""
    # Total trades
    result = await db.execute(
        select(func.count(BotTrade.id)).where(BotTrade.bot_id == bot_id)
    )
    total_trades = result.scalar() or 0

    if total_trades == 0:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "win_rate_pct": 0.0,
            "avg_trade_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
        }

    # Closed trades with PnL
    result = await db.execute(
        select(
            func.count(BotTrade.id),
            func.sum(BotTrade.pnl),
            func.avg(BotTrade.pnl),
            func.max(BotTrade.pnl),
            func.min(BotTrade.pnl),
        ).where(BotTrade.bot_id == bot_id, BotTrade.pnl.isnot(None))
    )
    row = result.one()
    closed_count = row[0] or 0
    total_pnl = float(row[1] or 0)
    avg_pnl = float(row[2] or 0)
    best = float(row[3] or 0)
    worst = float(row[4] or 0)

    # Winning trades
    result = await db.execute(
        select(func.count(BotTrade.id)).where(
            BotTrade.bot_id == bot_id, BotTrade.pnl > 0
        )
    )
    winning = result.scalar() or 0

    win_rate = (winning / closed_count * 100) if closed_count > 0 else 0.0

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate_pct": round(win_rate, 2),
        "avg_trade_pnl": round(avg_pnl, 2),
        "best_trade": round(best, 2),
        "worst_trade": round(worst, 2),
        "winning_trades": winning,
        "losing_trades": closed_count - winning,
    }
