"""Bot health check — detects zombie bots and sends email alerts.

Runs every 5 minutes. A bot is considered "broken" if:
- Status is "running" in the DB
- Market is open for its symbol
- No new log entries in the last 15 minutes (3x poll interval)

When detected, sends an email alert to the configured admin address.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import resend
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import get_settings
from ..models.bot import Bot
from ..models.bot_log import BotLog

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300  # 5 minutes
STALE_THRESHOLD_MINUTES = 15  # No logs in 15 min = broken
ALERT_COOLDOWN_SECONDS = 3600  # Don't re-alert for same bot within 1 hour

# Track last alert time per bot to avoid spam
_last_alert: dict[str, float] = {}


async def bot_health_check_loop(
    session_factory: async_sessionmaker,
    alert_email: str = "lomash@getpineforge.com",
):
    """Background loop that checks for zombie bots every 5 minutes."""
    await asyncio.sleep(60)  # Wait for server to start
    logger.info("Started bot health check loop (every 5 min, alert → %s)", alert_email)

    while True:
        try:
            await _check_bots(session_factory, alert_email)
        except Exception as e:
            logger.warning("Error during bot health check: %s", e)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _check_bots(session_factory: async_sessionmaker, alert_email: str):
    """Check all 'running' bots for signs of being stuck/broken."""
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    async with session_factory() as db:
        # Get all bots that claim to be running
        running_bots = (await db.execute(
            select(Bot).where(Bot.status == "running")
        )).scalars().all()

        if not running_bots:
            return

        for bot in running_bots:
            # Check if market is open for this symbol
            try:
                from pineforge.live.market_hours import is_market_likely_closed
                closed, reason = is_market_likely_closed(bot.symbol)
                if closed:
                    continue  # Market is closed — no logs expected
            except Exception:
                pass  # If market hours check fails, still check the bot

            # Check latest log entry
            latest_log = (await db.execute(
                select(func.max(BotLog.created_at)).where(BotLog.bot_id == bot.id)
            )).scalar()

            if latest_log is None:
                # No logs at all — started but never ran
                if bot.started_at and bot.started_at < stale_cutoff:
                    await _send_alert(alert_email, bot, "No logs since start", now)
                continue

            # Ensure timezone-aware comparison
            if latest_log.tzinfo is None:
                latest_log = latest_log.replace(tzinfo=timezone.utc)

            if latest_log < stale_cutoff:
                minutes_stale = int((now - latest_log).total_seconds() / 60)
                await _send_alert(
                    alert_email, bot,
                    f"No logs in {minutes_stale} minutes (last: {latest_log.strftime('%H:%M UTC')})",
                    now,
                )


async def _send_alert(alert_email: str, bot: Bot, issue: str, now: datetime):
    """Send an email alert for a broken bot (with cooldown to avoid spam)."""
    bot_key = str(bot.id)
    last = _last_alert.get(bot_key, 0)
    if (now.timestamp() - last) < ALERT_COOLDOWN_SECONDS:
        return  # Already alerted recently

    settings = get_settings()
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — cannot send bot health alert")
        return

    subject = f"[PineForge] Bot '{bot.name}' appears broken"
    html = f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#030712;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#030712;padding:40px 20px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="background-color:#111827;border:1px solid #1f2937;border-radius:16px;overflow:hidden;">
        <tr><td style="padding:32px 32px 0;text-align:center;">
          <span style="font-size:24px;font-weight:700;color:#ffffff;">&#127794; PineForge</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="margin:0 0 8px;font-size:20px;font-weight:600;color:#ef4444;">Bot Health Alert</h1>
          <p style="margin:0 0 16px;font-size:14px;color:#9ca3af;line-height:1.6;">
            A bot is showing as <strong style="color:#fbbf24;">running</strong> on the dashboard but appears to be stuck.
          </p>
          <table width="100%" cellpadding="8" cellspacing="0" style="background-color:#1f2937;border-radius:8px;font-size:13px;color:#d1d5db;">
            <tr><td style="color:#9ca3af;">Bot Name</td><td style="color:#fff;font-weight:600;">{bot.name}</td></tr>
            <tr><td style="color:#9ca3af;">Symbol</td><td style="color:#fff;">{bot.symbol} {bot.timeframe}</td></tr>
            <tr><td style="color:#9ca3af;">Started</td><td style="color:#fff;">{bot.started_at.strftime('%Y-%m-%d %H:%M UTC') if bot.started_at else 'N/A'}</td></tr>
            <tr><td style="color:#9ca3af;">Issue</td><td style="color:#ef4444;font-weight:600;">{issue}</td></tr>
            <tr><td style="color:#9ca3af;">Bot ID</td><td style="color:#6b7280;font-size:11px;">{bot.id}</td></tr>
          </table>
          <p style="margin:20px 0 0;font-size:13px;color:#6b7280;">
            The bot may need to be stopped and restarted. Check the server logs for details.
          </p>
        </td></tr>
        <tr><td style="padding:0 32px 32px;text-align:center;">
          <p style="margin:0;font-size:11px;color:#4b5563;">&copy; PineForge &mdash; Automated Trading Platform</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    try:
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": [alert_email],
            "subject": subject,
            "html": html,
        })
        _last_alert[bot_key] = now.timestamp()
        logger.info("Sent bot health alert for '%s' (%s) to %s: %s",
                     bot.name, bot.id, alert_email, issue)
    except Exception as e:
        logger.error("Failed to send bot health alert: %s", e)
