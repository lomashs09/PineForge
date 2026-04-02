"""Twelve Data provider for historical OHLCV — longer intraday history than yfinance.

yfinance limits: 5m/15m = 60 days, 1m = 7 days.
Twelve Data free tier: 5m/15m/1h = 1+ years, 1m = months.
800 API credits/day (1 credit per request).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from .data import DataFeed

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com/time_series"

# Twelve Data uses different symbol format
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAU/USD": "XAU/USD",
    "XAGUSD": "XAG/USD",
    "XAG/USD": "XAG/USD",
    "EURUSD": "EUR/USD",
    "EUR/USD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "GBP/USD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USD/JPY": "USD/JPY",
    "BTCUSD": "BTC/USD",
    "BTC/USD": "BTC/USD",
    "ETHUSD": "ETH/USD",
    "ETH/USD": "ETH/USD",
}

INTERVAL_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
    "1w": "1week",
    "1M": "1month",
}


def resolve_symbol(symbol: str) -> str:
    upper = symbol.upper().strip()
    return SYMBOL_MAP.get(upper, upper)


def download(
    symbol: str,
    start: str,
    end: str | None = None,
    interval: str = "5m",
    api_key: str = "",
) -> DataFeed:
    """Download OHLCV data from Twelve Data.

    Args:
        symbol: Trading symbol (e.g. "XAUUSD", "EURUSD", "AAPL")
        start: Start date "YYYY-MM-DD"
        end: End date "YYYY-MM-DD" or None for today
        interval: "1m", "5m", "15m", "1h", "4h", "1d"
        api_key: Twelve Data API key

    Returns:
        DataFeed ready for backtesting
    """
    if not api_key:
        raise ValueError("Twelve Data API key is required")

    td_symbol = resolve_symbol(symbol)
    td_interval = INTERVAL_MAP.get(interval, interval)

    params = {
        "symbol": td_symbol,
        "interval": td_interval,
        "start_date": start,
        "apikey": api_key,
        "order": "ASC",  # oldest first
        "outputsize": 5000,  # max per request
    }
    if end:
        params["end_date"] = end

    logger.info("Twelve Data: %s %s from %s to %s", td_symbol, td_interval, start, end)

    resp = requests.get(BASE_URL, params=params, timeout=30)
    data = resp.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message', 'Unknown error')}")

    values = data.get("values", [])
    if not values:
        raise ValueError(f"No data returned for {td_symbol} ({interval}) from {start}")

    bars = []
    for v in values:
        bars.append({
            "open": float(v["open"]),
            "high": float(v["high"]),
            "low": float(v["low"]),
            "close": float(v["close"]),
            "volume": int(float(v.get("volume", 0) or 0)),
            "date": v["datetime"],
        })

    logger.info("Twelve Data: got %d bars for %s", len(bars), td_symbol)
    return DataFeed(bars)
