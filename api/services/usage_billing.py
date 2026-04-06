"""Usage billing — periodic deduction from user balance based on active resources.

Runs every 5 minutes as a background task. For each user with running bots or
active accounts, calculates cost since last billing tick and deducts from balance.
Stops all bots when balance drops below $1.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ..models.bot import Bot
from ..models.broker_account import BrokerAccount
from ..models.user import User

logger = logging.getLogger(__name__)

# Rates (MetaAPI cost + 70% margin)
RATE_ACTIVE_BOT_PER_HOUR = 0.022
RATE_INACTIVE_ACCOUNT_PER_HOUR = 0.002
RATE_DEPLOYMENT = 0.13

BILLING_INTERVAL_SECONDS = 300  # 5 minutes
LOW_BALANCE_THRESHOLD = 1.0  # Stop bots when balance drops below this
MIN_BALANCE_TO_START = 5.0  # Minimum balance to start a bot


async def usage_billing_loop(session_factory: async_sessionmaker, bot_manager=None):
    """Background loop that deducts usage from user balances every 5 minutes."""
    # Wait for server to fully start
    await asyncio.sleep(30)
    print("[UsageBilling] Started usage billing loop (every 5 min)", flush=True)

    while True:
        try:
            await _billing_tick(session_factory, bot_manager)
        except Exception as e:
            print(f"[UsageBilling] Error: {e}", flush=True)

        await asyncio.sleep(BILLING_INTERVAL_SECONDS)


async def _billing_tick(session_factory: async_sessionmaker, bot_manager=None):
    """One billing tick — calculate and deduct usage for all users."""
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        # Get all users who have running bots or active accounts
        result = await db.execute(
            select(User).where(User.is_active == True)
        )
        users = result.scalars().all()

        for user in users:
            if user.is_admin:
                continue  # Admins are exempt from billing

            # Get running bots for this user
            bot_result = await db.execute(
                select(Bot).where(
                    Bot.user_id == user.id,
                    Bot.status == "running",
                )
            )
            running_bots = bot_result.scalars().all()

            # Get active accounts for this user
            acc_result = await db.execute(
                select(BrokerAccount).where(
                    BrokerAccount.user_id == user.id,
                    BrokerAccount.is_active == True,
                )
            )
            active_accounts = acc_result.scalars().all()

            if not running_bots and not active_accounts:
                continue

            # Calculate cost for this billing interval
            interval_hours = BILLING_INTERVAL_SECONDS / 3600

            # Only charge bots that have been running > 1 hour (first hour prepaid on start)
            billable_bots = [
                b for b in running_bots
                if b.started_at and (now - b.started_at).total_seconds() > 3600
            ]
            bot_cost = len(billable_bots) * RATE_ACTIVE_BOT_PER_HOUR * interval_hours
            account_cost = len(active_accounts) * RATE_INACTIVE_ACCOUNT_PER_HOUR * interval_hours
            total_cost = bot_cost + account_cost

            if total_cost <= 0:
                continue

            # Deduct from balance
            old_balance = user.balance or 0.0
            new_balance = old_balance - total_cost
            user.balance = round(new_balance, 4)

            if running_bots:
                print(f"[UsageBilling] {user.email}: deducted ${total_cost:.4f} "
                      f"({len(billable_bots)}/{len(running_bots)} billable bots, {len(active_accounts)} accounts) "
                      f"balance: ${old_balance:.2f} -> ${new_balance:.2f}", flush=True)

            # Check if balance is too low — stop all bots
            if new_balance < LOW_BALANCE_THRESHOLD and running_bots:
                print(f"[UsageBilling] {user.email}: balance ${new_balance:.2f} < ${LOW_BALANCE_THRESHOLD:.2f} "
                      f"— stopping {len(running_bots)} bots", flush=True)
                await _stop_user_bots(db, user.id, running_bots, bot_manager)

        await db.commit()


async def _stop_user_bots(db: AsyncSession, user_id, running_bots, bot_manager=None):
    """Stop all running bots for a user due to low balance."""
    for bot in running_bots:
        bot.status = "stopped"
        bot.stopped_at = datetime.now(timezone.utc)
        bot.error_message = "Stopped: insufficient balance. Add funds to restart."

        # Also stop in BotManager if running in-process
        if bot_manager:
            try:
                await bot_manager.stop_bot(bot.id)
            except Exception:
                pass  # Bot might not be in memory
