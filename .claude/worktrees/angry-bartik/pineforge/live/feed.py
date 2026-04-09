"""Live data feed — fetch OHLCV candles from MetaAPI."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..data import DataFeed

logger = logging.getLogger("pineforge.live.feed")

# MetaAPI timeframe strings map to approximate durations in seconds
TIMEFRAME_SECONDS = {
    "1m": 60, "2m": 120, "3m": 180, "5m": 300,
    "10m": 600, "15m": 900, "20m": 1200, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400,
    "1d": 86400, "1w": 604800,
}


async def fetch_candles(account, symbol: str, timeframe: str, limit: int = 200) -> list[dict[str, Any]]:
    """Fetch historical candles from MetaAPI.

    Args:
        account: MetaAPI MetatraderAccount instance.
        symbol: Trading symbol (e.g. "XAUUSDm").
        timeframe: Candle timeframe (e.g. "1h").
        limit: Number of candles to fetch (max 1000).

    Returns:
        List of bar dicts with open/high/low/close/volume/date keys.
    """
    try:
        candles = await asyncio.wait_for(
            account.get_historical_candles(symbol, timeframe, limit=min(limit, 1000)),
            timeout=30,
        )
    except asyncio.TimeoutError:
        logger.error("Candle fetch timed out after 30s")
        return []

    if not candles:
        logger.warning("No candles returned from MetaAPI for %s %s", symbol, timeframe)
        return []

    bars = []
    for c in candles:
        bars.append({
            "open": float(c.get("open", 0)),
            "high": float(c.get("high", 0)),
            "low": float(c.get("low", 0)),
            "close": float(c.get("close", 0)),
            "volume": float(c.get("tickVolume", c.get("volume", 0))),
            "date": c.get("time"),
        })

    bars.sort(key=lambda b: b["date"] if b["date"] else "")
    return bars


def bars_to_datafeed(bars: list[dict[str, Any]]) -> DataFeed:
    """Convert a list of bar dicts into a DataFeed for the engine."""
    return DataFeed(bars)


def detect_new_bar(bars: list[dict[str, Any]], last_bar_time: str | None) -> bool:
    """Check if the latest closed bar is newer than what we last processed."""
    if not bars or len(bars) < 2:
        return False
    # The last bar in the list might still be forming; use the second-to-last
    latest_closed = bars[-2]
    latest_time = latest_closed.get("date")
    if latest_time is None:
        return False
    return latest_time != last_bar_time


def get_latest_closed_bar_time(bars: list[dict[str, Any]]) -> str | None:
    """Get the timestamp of the most recent fully closed bar."""
    if len(bars) < 2:
        return None
    return bars[-2].get("date")
