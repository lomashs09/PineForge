"""Market hours awareness — know when markets are open/closed.

Used by the live bridge to adjust polling behavior during known closure
windows instead of treating empty data as an error.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Tuple

# ── Market schedules ────────────────────────────────────────────────
# Times are in UTC.  Each entry is (open_hour, open_minute, close_hour, close_minute).
# "close" means the daily maintenance break START; "open" is when it ends.
#
# Most forex/CFD brokers run on a New York 5 PM close:
#   Sunday 22:00 UTC  →  Friday 22:00 UTC  (continuous, with daily break)
#   Daily break: ~21:55 – 22:05 UTC (some brokers: 21:58 – 22:01 UTC)
#
# These are approximate — exact times depend on the broker and DST.

# (weekend_open_day, weekend_open_hour, weekend_close_day, weekend_close_hour)
# day: 0=Mon .. 6=Sun
_FOREX_WEEKEND = {
    "open_day": 6,   # Sunday
    "open_hour": 22,  # 22:00 UTC (5 PM ET)
    "close_day": 4,  # Friday
    "close_hour": 22, # 22:00 UTC (5 PM ET)
}

# Daily maintenance break (approximate, conservative)
_DAILY_BREAK_START_HOUR = 21  # 21:55 UTC
_DAILY_BREAK_START_MINUTE = 50
_DAILY_BREAK_END_HOUR = 22    # 22:10 UTC
_DAILY_BREAK_END_MINUTE = 15

# Symbols that follow forex hours (case-insensitive prefix matching)
_FOREX_SYMBOLS = [
    "xau", "xag", "xpt", "xpd",  # Metals
    "eur", "gbp", "aud", "nzd", "usd", "jpy", "chf", "cad",  # Forex majors
    "us30", "us500", "nas", "spx", "dji",  # US indices (often same hours)
    "ukx", "dax", "ftse",  # European indices
]

# Symbols that trade nearly 24/7 (crypto)
_CRYPTO_SYMBOLS = ["btc", "eth", "ltc", "xrp", "ada", "sol", "doge", "bnb"]


def _is_crypto(symbol: str) -> bool:
    """Check if symbol is a crypto pair (trades 24/7)."""
    sym = symbol.lower()
    return any(sym.startswith(c) for c in _CRYPTO_SYMBOLS)


def _is_forex_like(symbol: str) -> bool:
    """Check if symbol follows forex market hours."""
    sym = symbol.lower()
    return any(sym.startswith(f) for f in _FOREX_SYMBOLS)


def is_market_likely_closed(symbol: str, now: datetime | None = None) -> Tuple[bool, str]:
    """Check if the market for this symbol is likely closed right now.

    Returns:
        (is_closed, reason) — reason is a human-readable string.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        # Ensure we're working in UTC
        now = now.astimezone(timezone.utc)

    # Crypto trades 24/7
    if _is_crypto(symbol):
        return False, ""

    weekday = now.weekday()  # 0=Mon .. 6=Sun
    hour = now.hour
    minute = now.minute

    # ── Weekend check ────────────────────────────────────────────
    # Market is closed from Friday 22:00 UTC to Sunday 22:00 UTC
    if _is_forex_like(symbol) or True:  # Default: assume forex hours for unknown symbols
        # Saturday: always closed
        if weekday == 5:
            return True, "Weekend (market closed Saturday)"

        # Sunday before 22:00 UTC: still closed
        if weekday == 6 and hour < 22:
            return True, "Weekend (market opens Sunday ~22:00 UTC)"

        # Friday after 22:00 UTC: closed
        if weekday == 4 and hour >= 22:
            return True, "Weekend (market closed Friday ~22:00 UTC)"

        # ── Daily maintenance break ──────────────────────────────
        if _is_in_daily_break(hour, minute):
            return True, "Daily maintenance break (~21:55-22:15 UTC)"

    return False, ""


def _is_in_daily_break(hour: int, minute: int) -> bool:
    """Check if current time falls in the daily maintenance window."""
    # 21:50 to 22:15 UTC
    if hour == _DAILY_BREAK_START_HOUR and minute >= _DAILY_BREAK_START_MINUTE:
        return True
    if hour == _DAILY_BREAK_END_HOUR and minute <= _DAILY_BREAK_END_MINUTE:
        return True
    return False


def get_sleep_duration_for_closed_market(symbol: str, now: datetime | None = None) -> int:
    """Get recommended sleep duration (seconds) when market is known to be closed.

    During known closure windows, we poll less frequently to save resources.
    Returns a longer sleep than the normal poll interval.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    weekday = now.weekday()

    # During weekend: poll every 5 minutes (no point hammering the API)
    if weekday == 5:  # Saturday
        return 300
    if weekday == 6 and now.hour < 21:  # Sunday well before open
        return 300
    if weekday == 6 and now.hour < 22:  # Sunday close to open
        return 60  # Check more frequently near open time

    # During daily break: poll every 2 minutes
    return 120
