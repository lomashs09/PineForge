"""Periodically reconcile DB bot_trades with live broker positions.

Why this exists:
  Trades whose close event was missed (bot offline, MetaAPI disconnect,
  manual close on MT5, broker SL/TP hit while bot was retrying) accumulate
  as `closed_at IS NULL` in bot_trades even though the broker has closed
  them. Without reconciliation:
    - The user's trade history shows fake-open trades
    - close_all logic and "max open positions" guards see ghosts
    - PnL aggregations are wrong

What this does:
  Every RECONCILE_INTERVAL_SECONDS, for each broker account that has at
  least one running bot:
    1. Fetch live positions from MetaAPI (one RPC call per account)
    2. Find DB bot_trades for that account where closed_at IS NULL
    3. Any DB row whose order_id is NOT in the live position set is
       marked closed (closed_at = now()). exit_price/pnl stay NULL —
       we don't synthesise numbers we didn't observe.
  Each reconciled trade emits:
    - structured WARNING log with bot_id, order_id, symbol, age, reason
    - Sentry tags (bot_id, account_id) for grouping
    - metric `bot.trade.external_close` (tag: symbol, direction)
  Every cycle emits:
    - metric `reconciliation.runs` (count)
    - metric `reconciliation.trades_reconciled` (distribution)
    - metric `reconciliation.errors` (count) on per-account failure
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models.bot import Bot
from ..models.bot_trade import BotTrade
from ..models.broker_account import BrokerAccount
from ..utils import metrics

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 300  # 5 min


async def reconcile_once(
    session_factory: async_sessionmaker, metaapi_token: str
) -> dict:
    """Run a single reconciliation pass. Returns a stats dict."""
    stats = {
        "accounts_checked": 0,
        "trades_reconciled": 0,
        "errors": 0,
        "skipped_accounts": 0,
    }
    if not metaapi_token:
        stats["skipped_reason"] = "no_metaapi_token"
        return stats

    # 1) Find broker accounts that have at least one running bot
    async with session_factory() as db:
        result = await db.execute(
            select(BrokerAccount.id, BrokerAccount.metaapi_account_id)
            .where(
                BrokerAccount.id.in_(
                    select(Bot.broker_account_id)
                    .where(Bot.status == "running")
                    .distinct()
                )
            )
        )
        accounts = list(result.all())

    if not accounts:
        return stats

    # Lazy import — only when there's actually work to do
    from metaapi_cloud_sdk import MetaApi
    api = MetaApi(token=metaapi_token)

    for ba_id, metaapi_account_id in accounts:
        if not metaapi_account_id:
            stats["skipped_accounts"] += 1
            continue

        # Cheap pre-check: skip accounts with no DB-open trades to reconcile
        async with session_factory() as db:
            count_result = await db.execute(
                select(func.count(BotTrade.id)).where(
                    BotTrade.broker_account_id == ba_id,
                    BotTrade.closed_at.is_(None),
                )
            )
            db_open_count = count_result.scalar() or 0
        if db_open_count == 0:
            stats["skipped_accounts"] += 1
            continue

        try:
            account = await api.metatrader_account_api.get_account(metaapi_account_id)
            if account.state not in ("DEPLOYED", "DEPLOYING"):
                stats["skipped_accounts"] += 1
                continue
            await account.wait_connected()
            conn = account.get_rpc_connection()
            await conn.connect()
            await conn.wait_synchronized()

            live_positions = await conn.get_positions()
            live_order_ids = {str(p.get("id")) for p in live_positions if p.get("id")}
            stats["accounts_checked"] += 1

            await _reconcile_account(
                session_factory, ba_id, metaapi_account_id, live_order_ids, stats
            )

            try:
                await conn.close()
            except Exception:
                pass
        except Exception as e:
            stats["errors"] += 1
            # Tag in Sentry so account-level reconcile failures are diagnosable
            _set_sentry_tag("reconcile_account", str(metaapi_account_id))
            logger.warning(
                "Position reconciliation failed account=%s broker_account=%s: %s",
                metaapi_account_id, ba_id, e,
                exc_info=True,
            )
            metrics.count(
                "reconciliation.errors",
                1,
                attributes={"stage": "account_connect"},
            )

    return stats


async def _reconcile_account(
    session_factory: async_sessionmaker,
    ba_id,
    metaapi_account_id: str,
    live_order_ids: set,
    stats: dict,
) -> None:
    """Mark DB-open trades as closed when their order_id isn't in live_order_ids."""
    async with session_factory() as db:
        result = await db.execute(
            select(BotTrade).where(
                BotTrade.broker_account_id == ba_id,
                BotTrade.closed_at.is_(None),
            )
        )
        open_trades = list(result.scalars().all())

        if not open_trades:
            return

        reconciled = 0
        now = datetime.now(timezone.utc)
        for trade in open_trades:
            if not trade.order_id:
                # No order_id recorded — can't match. Log but don't touch.
                logger.warning(
                    "Open trade with no order_id: bot_id=%s id=%s symbol=%s opened=%s",
                    trade.bot_id, trade.id, trade.symbol, trade.opened_at,
                )
                continue
            if str(trade.order_id) in live_order_ids:
                continue  # Still open on broker — leave alone

            age_hours = (now - trade.opened_at).total_seconds() / 3600
            trade.closed_at = now
            # exit_price and pnl stay NULL — we don't know what we didn't see.

            # Structured log: every field needed to root-cause the desync.
            logger.warning(
                "Reconciled orphan trade: bot_id=%s order_id=%s symbol=%s "
                "direction=%s opened=%s age_hours=%.1f account=%s reason=external_close",
                trade.bot_id, trade.order_id, trade.symbol,
                trade.direction, trade.opened_at, age_hours, metaapi_account_id,
            )
            _set_sentry_tag("bot_id", str(trade.bot_id))
            _set_sentry_tag("order_id", str(trade.order_id))
            metrics.count(
                "bot.trade.external_close",
                1,
                attributes={
                    "symbol": trade.symbol or "unknown",
                    "direction": trade.direction or "unknown",
                },
            )
            reconciled += 1

        if reconciled:
            await db.commit()
            stats["trades_reconciled"] += reconciled


def _set_sentry_tag(key: str, value: str) -> None:
    try:
        import sentry_sdk
        sentry_sdk.set_tag(key, value)
    except Exception:
        pass


async def position_reconciliation_loop(
    session_factory: async_sessionmaker, metaapi_token: str
) -> None:
    """Loop forever, running reconcile_once every RECONCILE_INTERVAL_SECONDS."""
    logger.info(
        "Position reconciliation loop starting (interval=%ds)",
        RECONCILE_INTERVAL_SECONDS,
    )
    while True:
        try:
            stats = await reconcile_once(session_factory, metaapi_token)
            metrics.count("reconciliation.runs", 1)
            if stats.get("trades_reconciled", 0) > 0 or stats.get("errors", 0) > 0:
                logger.info("Reconciliation cycle: %s", stats)
                metrics.distribution(
                    "reconciliation.trades_reconciled",
                    stats["trades_reconciled"],
                )
        except asyncio.CancelledError:
            logger.info("Position reconciliation loop cancelled")
            raise
        except Exception:
            logger.exception("Position reconciliation cycle crashed")
            metrics.count(
                "reconciliation.errors",
                1,
                attributes={"stage": "cycle_top_level"},
            )

        try:
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
