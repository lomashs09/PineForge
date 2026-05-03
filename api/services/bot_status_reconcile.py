"""Periodically reconcile DB bot.status with BotManager runtime state.

Why this exists:
  Bot.status is set in the DB at lifecycle transitions (running/stopped/
  error). BotManager._running_bots holds the in-memory asyncio tasks.
  These two can drift:
    - API process restarts: DB still says 'running' but no task exists
      until restart_crashed_bots() catches up
    - A bot task crashes uncaught: removed from _running_bots, but DB
      remains 'running'
    - A user clicks Stop while the wrapper is mid-cleanup: brief window
      where the task is gone but DB hasn't committed 'stopped' yet

What this does:
  Every BOT_STATUS_RECONCILE_INTERVAL_SECONDS:
    1. For every Bot with status='running' AND started_at older than
       GRACE_PERIOD_SECONDS: if the bot is NOT in BotManager._running_bots,
       it's an orphan. Mark as 'error' with a clear message + emit a
       Sentry-tagged WARNING so the user knows their bot died silently.
    2. For every entry in BotManager._running_bots whose DB status is
       NOT 'running': log + emit metric. We don't auto-correct this side
       because the truth is genuinely ambiguous (the task IS running,
       but maybe a stop was requested). Operator visibility is enough.

Each reconciliation emits:
  - structured WARNING log per orphan
  - Sentry tags: bot_id, reconcile_kind=orphan_db_running|orphan_runtime
  - metric `bot.status.orphan` (count, by kind)

Cycle metrics:
  - `bot_status_reconciliation.runs` (count)
  - `bot_status_reconciliation.orphans` (distribution)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models.bot import Bot
from ..utils import metrics

logger = logging.getLogger(__name__)

BOT_STATUS_RECONCILE_INTERVAL_SECONDS = 60     # 1 min
GRACE_PERIOD_SECONDS = 120                     # don't flag bots within 2 min of start

# Hold off on the first reconciliation pass until BotManager.restart_crashed_bots
# has had a realistic chance to run. That helper sleeps 5s, then attempts up to
# 3 retries per bot with backoff — easily 60-90s of cold-start work. We must
# not flag bots as orphans while that's still in flight.
STARTUP_GRACE_SECONDS = 240                    # 4 min


async def reconcile_bot_status_once(
    session_factory: async_sessionmaker, bot_manager
) -> dict:
    """Run a single reconciliation pass. Returns a stats dict."""
    stats = {
        "db_running": 0,
        "runtime_running": 0,
        "orphans_db_running": 0,    # DB says running, runtime doesn't have it
        "orphans_runtime": 0,       # runtime has it, DB doesn't say running
        "errors": 0,
    }

    runtime_ids = set(bot_manager._running_bots.keys()) if bot_manager else set()
    stats["runtime_running"] = len(runtime_ids)

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=GRACE_PERIOD_SECONDS)

    try:
        from ..models.broker_account import BrokerAccount
        async with session_factory() as session:
            result = await session.execute(
                select(Bot, BrokerAccount.metaapi_account_id)
                .join(BrokerAccount, BrokerAccount.id == Bot.broker_account_id, isouter=True)
                .where(Bot.status == "running")
            )
            db_running_pairs = list(result.all())
            db_running_bots = [pair[0] for pair in db_running_pairs]
            stats["db_running"] = len(db_running_bots)

            db_running_ids = set()
            orphan_ids = []
            demo_skipped = 0
            for bot, metaapi_account_id in db_running_pairs:
                db_running_ids.add(bot.id)
                # Skip bots within the grace window — they may be mid-startup
                if bot.started_at and bot.started_at > cutoff:
                    continue
                # Skip demo bots (metaapi_account_id starts with "demo-").
                # These are seeded fixtures for product walkthroughs / video
                # recording — they have no real MetaAPI account, so they
                # will never be in BotManager._running_bots and would
                # otherwise be flagged on every cycle.
                if (metaapi_account_id or "").startswith("demo-"):
                    demo_skipped += 1
                    continue
                if bot.id not in runtime_ids:
                    orphan_ids.append(bot.id)
            if demo_skipped:
                stats["demo_skipped"] = demo_skipped

            for bot in db_running_bots:
                if bot.id in orphan_ids:
                    age_minutes = 0.0
                    if bot.started_at:
                        age_minutes = (
                            datetime.now(timezone.utc) - bot.started_at
                        ).total_seconds() / 60
                    logger.warning(
                        "Bot status orphan: bot_id=%s name=%s status=running but "
                        "not in BotManager (age=%.1fmin) — marking error",
                        bot.id, bot.name, age_minutes,
                    )
                    bot.status = "error"
                    bot.error_message = (
                        "Bot disappeared from process memory without a clean "
                        "stop. Click Start to retry."
                    )
                    bot.stopped_at = datetime.now(timezone.utc)
                    _set_sentry_tag("bot_id", str(bot.id))
                    _set_sentry_tag("reconcile_kind", "orphan_db_running")
                    metrics.count(
                        "bot.status.orphan",
                        1,
                        attributes={"kind": "db_running_no_runtime"},
                    )
                    stats["orphans_db_running"] += 1

            if stats["orphans_db_running"]:
                await session.commit()

        # Detect runtime-orphans: runtime has bots whose DB status isn't 'running'.
        if runtime_ids - db_running_ids:
            async with session_factory() as session:
                result = await session.execute(
                    select(Bot.id, Bot.name, Bot.status)
                    .where(Bot.id.in_(list(runtime_ids - db_running_ids)))
                )
                for row in result.all():
                    bid, name, status = row
                    logger.warning(
                        "Bot status orphan: bot_id=%s name=%s in runtime but "
                        "DB status=%s (not 'running')",
                        bid, name, status,
                    )
                    _set_sentry_tag("bot_id", str(bid))
                    _set_sentry_tag("reconcile_kind", "orphan_runtime")
                    metrics.count(
                        "bot.status.orphan",
                        1,
                        attributes={"kind": "runtime_db_not_running"},
                    )
                    stats["orphans_runtime"] += 1

    except Exception:
        stats["errors"] += 1
        logger.exception("Bot status reconciliation pass crashed")
        metrics.count(
            "bot_status_reconciliation.errors",
            1,
            attributes={"stage": "pass"},
        )

    return stats


def _set_sentry_tag(key: str, value: str) -> None:
    try:
        import sentry_sdk
        sentry_sdk.set_tag(key, value)
    except Exception:
        pass


async def bot_status_reconciliation_loop(
    session_factory: async_sessionmaker, bot_manager
) -> None:
    """Loop forever, running reconcile_bot_status_once on a fixed cadence.

    Sleeps STARTUP_GRACE_SECONDS before the first pass so that
    BotManager.restart_crashed_bots() — which itself sleeps 5s and then
    tries up to 3 reconnect attempts per bot — has enough time to pick
    up bots that were running before an API restart. Without this, the
    loop would falsely flag pre-restart bots as orphans and mark them
    'error' before they could be auto-restarted.
    """
    logger.info(
        "Bot status reconciliation loop starting (interval=%ds, grace=%ds, startup_grace=%ds)",
        BOT_STATUS_RECONCILE_INTERVAL_SECONDS, GRACE_PERIOD_SECONDS, STARTUP_GRACE_SECONDS,
    )
    try:
        await asyncio.sleep(STARTUP_GRACE_SECONDS)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            stats = await reconcile_bot_status_once(session_factory, bot_manager)
            metrics.count("bot_status_reconciliation.runs", 1)
            total_orphans = stats["orphans_db_running"] + stats["orphans_runtime"]
            if total_orphans > 0 or stats["errors"] > 0:
                logger.info("Bot status reconciliation cycle: %s", stats)
                metrics.distribution(
                    "bot_status_reconciliation.orphans",
                    total_orphans,
                )
        except asyncio.CancelledError:
            logger.info("Bot status reconciliation loop cancelled")
            raise
        except Exception:
            logger.exception("Bot status reconciliation cycle crashed")
            metrics.count(
                "bot_status_reconciliation.errors",
                1,
                attributes={"stage": "cycle_top_level"},
            )

        try:
            await asyncio.sleep(BOT_STATUS_RECONCILE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
