"""Periodically reconcile DB bot_trades with live broker positions.

Why this exists:
  Trades whose close event was missed (bot offline, MetaAPI disconnect,
  manual close on MT5, broker SL/TP hit while bot was retrying) accumulate
  with `closed_at IS NULL` even though the broker has closed them.
  Without reconciliation:
    - The user's trade history shows fake-open trades
    - close_all logic and "max open positions" guards see ghosts
    - PnL aggregations are wrong

What this does (per cycle, every RECONCILE_INTERVAL_SECONDS):
  For each broker account that has at least one running bot:
    1. Fetch live positions from MetaAPI (one RPC call per account)
    2. Fetch closed deals in the last DEAL_LOOKBACK_HOURS so we can
       backfill exit_price/pnl on orphans (Phase 2)
    3. Find DB bot_trades for that account whose lifecycle_state='open'
    4. Any DB row whose order_id is NOT in the live position set is
       marked lifecycle_state='reconciled_external'. If a matching
       close deal is found, exit_price/pnl are filled from it.

Each reconciled trade emits:
  - structured WARNING log with bot_id, order_id, symbol, age, reason,
    recovered_pnl flag (so you can grep journalctl for any of these)
  - Sentry tags (bot_id, order_id) for filtering
  - metric `bot.trade.external_close` (tag: symbol, direction, recovered_pnl)
Every cycle emits:
  - metric `reconciliation.runs` (count)
  - metric `reconciliation.trades_reconciled` (distribution)
  - metric `reconciliation.errors` (count, by stage)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models.bot import Bot
from ..models.bot_trade import BotTrade
from ..models.broker_account import BrokerAccount
from ..utils import metrics

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 300  # 5 min
DEAL_LOOKBACK_HOURS = 48          # window we ask MetaAPI for close deals
DEAL_FETCH_TIMEOUT = 30           # per-call seconds
DEAL_FETCH_RETRIES = 2            # extra attempts on transient failure


async def reconcile_once(
    session_factory: async_sessionmaker, metaapi_token: str
) -> dict:
    """Run a single reconciliation pass. Returns a stats dict."""
    stats = {
        "accounts_checked": 0,
        "trades_reconciled": 0,
        "trades_with_pnl": 0,
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

        # Demo bots are seeded fixtures with synthetic metaapi_account_ids
        # (prefix "demo-") used for product walkthroughs. They have no real
        # MetaAPI account — calling get_account 404s, which triggers the
        # "Get positions failed: This connection has been closed" cascade
        # in Sentry (PYTHON-FASTAPI-W). Skip them. Mirrors the same guard
        # in bot_status_reconcile.
        if metaapi_account_id.startswith("demo-"):
            stats["skipped_accounts"] += 1
            continue

        # Cheap pre-check: skip accounts with no DB-open trades to reconcile
        async with session_factory() as db:
            count_result = await db.execute(
                select(func.count(BotTrade.id)).where(
                    BotTrade.broker_account_id == ba_id,
                    BotTrade.lifecycle_state == "open",
                )
            )
            db_open_count = count_result.scalar() or 0
        if db_open_count == 0:
            stats["skipped_accounts"] += 1
            continue

        conn = None
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

            close_deals_by_position = await _fetch_close_deals(
                conn, metaapi_account_id, stats
            )

            stats["accounts_checked"] += 1

            await _reconcile_account(
                session_factory,
                ba_id,
                metaapi_account_id,
                live_order_ids,
                close_deals_by_position,
                stats,
            )
        except Exception as e:
            stats["errors"] += 1
            with _sentry_scope({"reconcile_account": str(metaapi_account_id)}):
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
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

    return stats


async def _fetch_close_deals(
    conn, metaapi_account_id: str, stats: dict
) -> Dict[str, dict]:
    """Fetch close deals in the lookback window keyed by positionId.

    Resilient to transient MetaAPI errors: retries DEAL_FETCH_RETRIES times
    with exponential backoff. On final failure, returns {} so reconciliation
    still proceeds — orphans get closed with NULL pnl rather than blocking.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=DEAL_LOOKBACK_HOURS)

    last_exc: Optional[Exception] = None
    for attempt in range(DEAL_FETCH_RETRIES + 1):
        try:
            deals = await asyncio.wait_for(
                conn.get_deals_by_time_range(start, end),
                timeout=DEAL_FETCH_TIMEOUT,
            )
            return _index_close_deals(deals)
        except asyncio.TimeoutError as e:
            last_exc = e
            logger.warning(
                "MetaAPI get_deals_by_time_range timed out account=%s attempt=%d",
                metaapi_account_id, attempt + 1,
            )
        except Exception as e:
            last_exc = e
            logger.warning(
                "MetaAPI get_deals_by_time_range failed account=%s attempt=%d: %s",
                metaapi_account_id, attempt + 1, e,
            )

        if attempt < DEAL_FETCH_RETRIES:
            await asyncio.sleep(2 ** attempt)  # 1s, then 2s

    stats["errors"] += 1
    metrics.count(
        "reconciliation.errors",
        1,
        attributes={"stage": "fetch_deals"},
    )
    logger.warning(
        "Giving up on deal fetch for account=%s after %d attempts (last: %s) — "
        "orphans will be closed with NULL pnl this cycle",
        metaapi_account_id, DEAL_FETCH_RETRIES + 1, last_exc,
    )
    return {}


def _index_close_deals(deals_response) -> Dict[str, dict]:
    """Normalise the SDK's deal list into {positionId: close_deal}.

    A position can have multiple deals (open, partial close, full close).
    We want the one whose entryType marks it as the closing deal.
    """
    if isinstance(deals_response, dict):
        deal_list = deals_response.get("deals", [])
    else:
        deal_list = deals_response or []

    result: Dict[str, dict] = {}
    for d in deal_list:
        position_id = d.get("positionId")
        if not position_id:
            continue
        entry = (d.get("entryType") or "").upper()
        # MetaAPI uses DEAL_ENTRY_OUT for full closes, DEAL_ENTRY_OUT_BY for
        # close-by-opposite. Both terminate the position.
        if "OUT" not in entry:
            continue
        # Prefer the latest close deal if multiple exist (partial closes
        # produce more than one OUT deal).
        existing = result.get(str(position_id))
        if existing is None or _deal_time(d) > _deal_time(existing):
            result[str(position_id)] = d
    return result


def _deal_time(deal: dict):
    t = deal.get("time")
    if isinstance(t, datetime):
        return t
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


async def _reconcile_account(
    session_factory: async_sessionmaker,
    ba_id,
    metaapi_account_id: str,
    live_order_ids: set,
    close_deals_by_position: Dict[str, dict],
    stats: dict,
) -> None:
    """Mark DB-open trades as closed when their order_id isn't in live_order_ids.

    When a matching close deal is found in close_deals_by_position, also
    backfill exit_price and pnl from the deal's price/profit fields.
    """
    async with session_factory() as db:
        result = await db.execute(
            select(BotTrade).where(
                BotTrade.broker_account_id == ba_id,
                BotTrade.lifecycle_state == "open",
            )
        )
        open_trades = list(result.scalars().all())

        if not open_trades:
            return

        reconciled = 0
        with_pnl = 0
        now = datetime.now(timezone.utc)
        for trade in open_trades:
            if not trade.order_id:
                logger.warning(
                    "Open trade with no order_id: bot_id=%s id=%s symbol=%s opened=%s",
                    trade.bot_id, trade.id, trade.symbol, trade.opened_at,
                )
                continue
            if str(trade.order_id) in live_order_ids:
                continue  # Still open on broker — leave alone

            age_hours = (now - trade.opened_at).total_seconds() / 3600

            close_deal = close_deals_by_position.get(str(trade.order_id))
            recovered_pnl = False
            if close_deal is not None:
                exit_price = close_deal.get("price")
                pnl = close_deal.get("profit")
                if exit_price is not None:
                    try:
                        trade.exit_price = Decimal(str(exit_price))
                    except Exception:
                        logger.warning(
                            "Could not parse exit_price=%r for order_id=%s",
                            exit_price, trade.order_id,
                        )
                if pnl is not None:
                    try:
                        trade.pnl = Decimal(str(pnl))
                        recovered_pnl = True
                        with_pnl += 1
                    except Exception:
                        logger.warning(
                            "Could not parse pnl=%r for order_id=%s",
                            pnl, trade.order_id,
                        )
                # Prefer the deal's actual close time when available
                deal_time = _deal_time(close_deal)
                if deal_time != datetime.min.replace(tzinfo=timezone.utc):
                    trade.closed_at = deal_time
                else:
                    trade.closed_at = now
            else:
                trade.closed_at = now

            trade.lifecycle_state = "reconciled_external"

            with _sentry_scope({
                "bot_id": str(trade.bot_id),
                "order_id": str(trade.order_id),
            }):
                logger.warning(
                    "Reconciled orphan trade: bot_id=%s order_id=%s symbol=%s "
                    "direction=%s opened=%s age_hours=%.1f account=%s "
                    "recovered_pnl=%s pnl=%s exit_price=%s reason=external_close",
                    trade.bot_id, trade.order_id, trade.symbol,
                    trade.direction, trade.opened_at, age_hours, metaapi_account_id,
                    recovered_pnl, trade.pnl, trade.exit_price,
                )
            metrics.count(
                "bot.trade.external_close",
                1,
                attributes={
                    "symbol": trade.symbol or "unknown",
                    "direction": trade.direction or "unknown",
                    "recovered_pnl": recovered_pnl,
                },
            )
            reconciled += 1

        if reconciled:
            try:
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to commit reconciliation updates for account=%s",
                    metaapi_account_id,
                )
                metrics.count(
                    "reconciliation.errors",
                    1,
                    attributes={"stage": "db_commit"},
                )
                return
            stats["trades_reconciled"] += reconciled
            stats["trades_with_pnl"] += with_pnl


from contextlib import contextmanager


@contextmanager
def _sentry_scope(tags: dict):
    """Push a Sentry scope so the tags only apply to events captured
    inside the `with` block, then auto-restore on exit.

    Why this matters: setting tags via the global hub (sentry_sdk.set_tag)
    leaks them across async tasks. Earlier the position_reconcile loop
    set reconcile_account="demo-..." on the hub and an unrelated
    pineforge.live.executor error fired from a different bot's task
    inherited the wrong tag — making Sentry triage misleading
    (PYTHON-FASTAPI-W tagged with reconcile_account=demo-...). Scoped
    tags eliminate that leakage.

    No-op if sentry_sdk isn't installed.
    """
    try:
        import sentry_sdk
    except Exception:
        yield
        return
    with sentry_sdk.push_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, v)
        yield


def _set_sentry_tag(key: str, value: str) -> None:
    """Deprecated — use _sentry_scope() instead. Kept as a no-op to avoid
    breaking any caller mid-refactor; the tag is set on the global hub
    and WILL leak across async tasks. Replace remaining callers with
    `with _sentry_scope({key: value}):` blocks."""
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
        "Position reconciliation loop starting (interval=%ds, deal_lookback=%dh)",
        RECONCILE_INTERVAL_SECONDS, DEAL_LOOKBACK_HOURS,
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
                if stats.get("trades_reconciled", 0) > 0:
                    metrics.distribution(
                        "reconciliation.trades_with_pnl",
                        stats["trades_with_pnl"],
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
