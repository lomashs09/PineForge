"""Symbol configuration — maps between display names, backtest tickers, and MT5 symbols.

Exness MT5 uses 'm' suffix for micro accounts (XAUUSDm, EURUSDm, etc).
yfinance uses different tickers (GC=F for gold, EURUSD=X for forex).
Twelve Data uses slash format (XAU/USD, EUR/USD).

This module provides a single source of truth for all symbol mappings.
"""

# Each symbol entry:
#   display  — shown to users in the UI
#   mt5      — exact Exness MT5 symbol name (for live trading)
#   yfinance — yfinance ticker (for backtesting daily/hourly)
#   twelvedata — Twelve Data symbol (for backtesting intraday)
#   category — for UI grouping

SYMBOLS = [
    # Commodities
    {
        "display": "XAUUSD",
        "name": "Gold",
        "mt5": "XAUUSDm",
        "yfinance": "GC=F",
        "twelvedata": "XAU/USD",
        "category": "commodity",
    },
    {
        "display": "XAGUSD",
        "name": "Silver",
        "mt5": "XAGUSDm",
        "yfinance": "SI=F",
        "twelvedata": "XAG/USD",
        "category": "commodity",
    },
    # Forex
    {
        "display": "EURUSD",
        "name": "EUR/USD",
        "mt5": "EURUSDm",
        "yfinance": "EURUSD=X",
        "twelvedata": "EUR/USD",
        "category": "forex",
    },
    {
        "display": "GBPUSD",
        "name": "GBP/USD",
        "mt5": "GBPUSDm",
        "yfinance": "GBPUSD=X",
        "twelvedata": "GBP/USD",
        "category": "forex",
    },
    {
        "display": "USDJPY",
        "name": "USD/JPY",
        "mt5": "USDJPYm",
        "yfinance": "JPY=X",
        "twelvedata": "USD/JPY",
        "category": "forex",
    },
    {
        "display": "USDCHF",
        "name": "USD/CHF",
        "mt5": "USDCHFm",
        "yfinance": "CHF=X",
        "twelvedata": "USD/CHF",
        "category": "forex",
    },
    {
        "display": "AUDUSD",
        "name": "AUD/USD",
        "mt5": "AUDUSDm",
        "yfinance": "AUDUSD=X",
        "twelvedata": "AUD/USD",
        "category": "forex",
    },
    {
        "display": "NZDUSD",
        "name": "NZD/USD",
        "mt5": "NZDUSDm",
        "yfinance": "NZDUSD=X",
        "twelvedata": "NZD/USD",
        "category": "forex",
    },
    # Crypto
    {
        "display": "BTCUSD",
        "name": "Bitcoin",
        "mt5": "BTCUSDm",
        "yfinance": "BTC-USD",
        "twelvedata": "BTC/USD",
        "category": "crypto",
    },
    {
        "display": "ETHUSD",
        "name": "Ethereum",
        "mt5": "ETHUSDm",
        "yfinance": "ETH-USD",
        "twelvedata": "ETH/USD",
        "category": "crypto",
    },
    # Indices
    {
        "display": "US30",
        "name": "Dow Jones",
        "mt5": "US30m",
        "yfinance": "^DJI",
        "twelvedata": "DJI",
        "category": "index",
    },
    {
        "display": "US500",
        "name": "S&P 500",
        "mt5": "US500m",
        "yfinance": "^GSPC",
        "twelvedata": "SPX",
        "category": "index",
    },
    {
        "display": "USTEC",
        "name": "Nasdaq 100",
        "mt5": "USTECm",
        "yfinance": "^IXIC",
        "twelvedata": "IXIC",
        "category": "index",
    },
]


def get_mt5_symbol(display: str) -> str:
    """Convert display symbol to MT5 symbol (e.g. XAUUSD → XAUUSDm)."""
    for s in SYMBOLS:
        if s["display"] == display.upper():
            return s["mt5"]
    # If not found, return as-is (user might have typed the MT5 name directly)
    return display


def get_backtest_symbol(display: str) -> str:
    """Convert display symbol to yfinance ticker (e.g. XAUUSD → GC=F)."""
    for s in SYMBOLS:
        if s["display"] == display.upper():
            return s["yfinance"]
    return display


def get_twelvedata_symbol(display: str) -> str:
    """Convert display symbol to Twelve Data format (e.g. XAUUSD → XAU/USD)."""
    for s in SYMBOLS:
        if s["display"] == display.upper():
            return s["twelvedata"]
    return display


def get_symbols_for_api() -> list:
    """Return symbol list for the frontend API."""
    return [
        {
            "symbol": s["display"],
            "name": s["name"],
            "mt5": s["mt5"],
            "category": s["category"],
        }
        for s in SYMBOLS
    ]
