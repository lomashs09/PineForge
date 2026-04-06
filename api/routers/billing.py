"""Billing routes — usage tracking and invoice history."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.bot import Bot
from ..models.bot_log import BotLog
from ..models.broker_account import BrokerAccount
from ..models.user import User

router = APIRouter(prefix="/api/billing", tags=["billing"])

# Rates (MetaAPI cost + 70% margin)
RATE_ACTIVE_BOT_PER_HOUR = 0.022
RATE_INACTIVE_ACCOUNT_PER_HOUR = 0.002
RATE_DEPLOYMENT = 0.13
RATE_ACCOUNT_SETUP = 3.00


@router.get("/usage")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current billing period usage for the authenticated user."""
    now = datetime.now(timezone.utc)
    # Current billing period: start of current month
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get all user's bots
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user.id)
    )
    bots = result.scalars().all()

    # Calculate active bot hours this period
    active_bot_hours = 0.0
    active_bots = []
    deployments = 0

    for bot in bots:
        started = bot.started_at
        stopped = bot.stopped_at

        # Count deployments (starts) this period
        if started and started >= period_start:
            deployments += 1

        # Calculate running hours
        if bot.status == "running" and started:
            # Currently running — calculate from start (or period start) to now
            effective_start = max(started, period_start)
            hours = (now - effective_start).total_seconds() / 3600
            active_bot_hours += hours
            active_bots.append({
                "id": str(bot.id),
                "name": bot.name,
                "symbol": bot.symbol,
                "timeframe": bot.timeframe,
                "hours_running": round(hours, 2),
            })
        elif stopped and started:
            # Stopped bot — calculate overlap with current period
            effective_start = max(started, period_start)
            effective_stop = stopped
            if effective_stop > period_start:
                hours = max(0, (effective_stop - effective_start).total_seconds() / 3600)
                active_bot_hours += hours

    # Get user's broker accounts
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.user_id == current_user.id,
            BrokerAccount.is_active == True,
        )
    )
    accounts = result.scalars().all()

    # Calculate account hosting hours
    inactive_account_hours = 0.0
    active_accounts = []
    for acc in accounts:
        # Account is "connected" since creation
        effective_start = max(acc.created_at, period_start)
        hours = (now - effective_start).total_seconds() / 3600
        inactive_account_hours += hours
        active_accounts.append({
            "id": str(acc.id),
            "label": acc.label,
            "mt5_login": acc.mt5_login,
            "mt5_server": acc.mt5_server,
            "hours_connected": round(hours, 2),
        })

    # Calculate costs
    active_bot_cost = active_bot_hours * RATE_ACTIVE_BOT_PER_HOUR
    inactive_account_cost = inactive_account_hours * RATE_INACTIVE_ACCOUNT_PER_HOUR
    deployment_cost = deployments * RATE_DEPLOYMENT
    total_cost = active_bot_cost + inactive_account_cost + deployment_cost

    # Build invoice history (simplified — last 3 months estimates)
    invoices = []
    for months_ago in range(1, 4):
        inv_end = period_start - timedelta(days=1)
        inv_end = inv_end.replace(day=1) if months_ago > 1 else period_start
        inv_start = (period_start - timedelta(days=30 * months_ago)).replace(day=1)

        # Count bot hours and deployments for that period
        period_bot_hours = 0.0
        period_deployments = 0
        for bot in bots:
            if bot.started_at and bot.started_at < inv_end:
                s = max(bot.started_at, inv_start)
                e = bot.stopped_at if bot.stopped_at and bot.stopped_at < inv_end else inv_end
                if e > s:
                    period_bot_hours += (e - s).total_seconds() / 3600
                if bot.started_at >= inv_start:
                    period_deployments += 1

        if period_bot_hours > 0 or period_deployments > 0:
            amount = (period_bot_hours * RATE_ACTIVE_BOT_PER_HOUR +
                      period_deployments * RATE_DEPLOYMENT +
                      len(accounts) * 30 * 24 * RATE_INACTIVE_ACCOUNT_PER_HOUR)
            invoices.append({
                "period": inv_start.strftime("%b %Y"),
                "bot_hours": round(period_bot_hours, 1),
                "deployments": period_deployments,
                "amount": round(amount, 2),
                "status": "paid",
            })

    return {
        "period_start": period_start.isoformat(),
        "active_bot_hours": round(active_bot_hours, 2),
        "active_bot_cost": round(active_bot_cost, 2),
        "inactive_account_hours": round(inactive_account_hours, 2),
        "inactive_account_cost": round(inactive_account_cost, 2),
        "deployments": deployments,
        "deployment_cost": round(deployment_cost, 2),
        "total_cost": round(total_cost, 2),
        "active_bots": active_bots,
        "active_accounts": active_accounts,
        "invoices": invoices,
    }
