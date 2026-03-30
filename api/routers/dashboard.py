"""Dashboard route — aggregate stats for the current user."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.bot import Bot
from ..models.bot_trade import BotTrade
from ..models.broker_account import BrokerAccount
from ..models.user import User
from ..schemas.dashboard import DashboardResponse

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Bot counts
    result = await db.execute(
        select(func.count(Bot.id)).where(Bot.user_id == current_user.id)
    )
    total_bots = result.scalar() or 0

    result = await db.execute(
        select(func.count(Bot.id)).where(
            Bot.user_id == current_user.id, Bot.status == "running"
        )
    )
    active_bots = result.scalar() or 0

    # Broker account count
    result = await db.execute(
        select(func.count(BrokerAccount.id)).where(
            BrokerAccount.user_id == current_user.id,
            BrokerAccount.is_active == True,
        )
    )
    broker_accounts = result.scalar() or 0

    # Get all bot IDs for this user
    result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user.id)
    )
    user_bot_ids = [row[0] for row in result.all()]

    total_pnl = 0.0
    today_pnl = 0.0
    total_trades = 0
    winning_trades = 0

    if user_bot_ids:
        # Total PnL and trades
        result = await db.execute(
            select(
                func.count(BotTrade.id),
                func.coalesce(func.sum(BotTrade.pnl), 0),
            ).where(BotTrade.bot_id.in_(user_bot_ids), BotTrade.pnl.isnot(None))
        )
        row = result.one()
        total_trades = row[0] or 0
        total_pnl = float(row[1] or 0)

        # Winning trades
        result = await db.execute(
            select(func.count(BotTrade.id)).where(
                BotTrade.bot_id.in_(user_bot_ids), BotTrade.pnl > 0
            )
        )
        winning_trades = result.scalar() or 0

        # Today's PnL
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.coalesce(func.sum(BotTrade.pnl), 0)).where(
                BotTrade.bot_id.in_(user_bot_ids),
                BotTrade.pnl.isnot(None),
                BotTrade.closed_at >= today_start,
            )
        )
        today_pnl = float(result.scalar() or 0)

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    return DashboardResponse(
        active_bots=active_bots,
        total_bots=total_bots,
        broker_accounts=broker_accounts,
        today_pnl=round(today_pnl, 2),
        total_pnl=round(total_pnl, 2),
        total_trades=total_trades,
        win_rate_pct=round(win_rate, 2),
    )
