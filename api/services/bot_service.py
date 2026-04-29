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
    # Check balance (minimum $5 to create a bot)
    if not user.is_admin and user.balance < 5.0:
        return f"Insufficient balance (${user.balance:.2f}). Minimum $5.00 required to create a bot. Please add funds in the Billing section."

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
            BrokerAccount.is_active.is_(True),
        )
    )
    if result.scalar_one_or_none() is None:
        return "Broker account not found or not owned by you"

    # Enforce one bot per broker account.
    # Why: positions and deal history on MT5 are scoped to the broker
    # account, not to the bot. When two bots share an account, the
    # /positions and /history endpoints have to filter by magic_number,
    # and brokers (e.g. Exness) strip magic on close deals — leading to
    # silent leaks and double-counted PnL on the dashboard.
    # Hard-enforcing one-bot-per-account at create time eliminates that
    # whole bug class. Existing duplicate setups are grandfathered (the
    # check fires only on NEW creates).
    result = await db.execute(
        select(Bot.id, Bot.name).where(
            Bot.broker_account_id == broker_account_id,
        )
    )
    existing = result.first()
    if existing is not None:
        return (
            f"This broker account is already used by bot '{existing.name}'. "
            "Each broker account can host only one bot — delete the existing "
            "bot first, or connect another broker account."
        )

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
    """Aggregate trade statistics for a bot in a single query.

    Each completed trade is recorded TWICE in bot_trades:
      * One entry row (signal LIKE 'entry_%'), which gets exit_price
        and pnl filled in by the streaming trade listener (Phase 3) or
        position reconciliation (Phase 2) when the position closes.
      * One legacy close-all summary row (signal='close', order_id='close-all'),
        written by BotPrintCapture from the bot's stdout.

    Both rows carry the same realized pnl, so summing all rows
    DOUBLE-COUNTS — that's the dashboard-vs-history discrepancy users hit.
    The entry row is the canonical source: it's the only path that
    survives reconciliation of external closes (close-all rows are never
    written for those), so we filter to entry-style signals here and
    everywhere we aggregate PnL.
    """
    result = await db.execute(
        select(
            func.count(BotTrade.id),
            func.coalesce(func.sum(BotTrade.pnl), 0),
            func.avg(BotTrade.pnl),
            func.max(BotTrade.pnl),
            func.min(BotTrade.pnl),
            func.count(BotTrade.id).filter(BotTrade.pnl > 0),
        ).where(
            BotTrade.bot_id == bot_id,
            BotTrade.pnl.isnot(None),
            BotTrade.signal.like("entry_%"),
        )
    )
    row = result.one()
    closed_count = row[0] or 0
    total_pnl = float(row[1] or 0)
    avg_pnl = float(row[2] or 0)
    best = float(row[3] or 0)
    worst = float(row[4] or 0)
    winning = row[5] or 0

    # Total trade count = entry rows only (each entry == one trade attempt;
    # close-all rows are duplicate summaries, not separate trades).
    total_result = await db.execute(
        select(func.count(BotTrade.id)).where(
            BotTrade.bot_id == bot_id,
            BotTrade.signal.like("entry_%"),
        )
    )
    total_trades = total_result.scalar() or 0

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
