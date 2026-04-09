"""OHLCV data loading — CSV files and yfinance downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


class DataFeed:
    """Bar-indexed access to OHLCV data."""

    def __init__(self, bars: list[dict[str, Any]]):
        self._bars = bars

    def __len__(self) -> int:
        return len(self._bars)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self._bars[index]

    @property
    def dates(self) -> list[Any]:
        return [b.get("date") for b in self._bars]


# Common column name mappings for auto-detection
_COLUMN_ALIASES = {
    "open": ["open", "Open", "OPEN", "o"],
    "high": ["high", "High", "HIGH", "h"],
    "low": ["low", "Low", "LOW", "l"],
    "close": ["close", "Close", "CLOSE", "c"],
    "volume": ["volume", "Volume", "VOLUME", "vol", "Vol", "v"],
    "date": ["date", "Date", "DATE", "datetime", "Datetime", "time", "Time",
             "timestamp", "Timestamp", "TIMESTAMP"],
}


def _find_column(df: pd.DataFrame, target: str) -> str | None:
    """Find a column name in the DataFrame matching one of the known aliases."""
    aliases = _COLUMN_ALIASES.get(target, [target])
    for alias in aliases:
        if alias in df.columns:
            return alias
    return None


def load_csv(path: str | Path, date_col: str | None = None) -> DataFeed:
    """Load OHLCV data from a CSV file.

    Auto-detects column names from common formats (TradingView, Yahoo Finance, generic).
    """
    path = Path(path)
    df = pd.read_csv(path)

    col_map: dict[str, str] = {}
    for target in ("open", "high", "low", "close", "volume", "date"):
        found = _find_column(df, target)
        if found:
            col_map[target] = found

    if date_col:
        col_map["date"] = date_col

    for required in ("open", "high", "low", "close"):
        if required not in col_map:
            raise ValueError(
                f"Could not find '{required}' column in CSV. "
                f"Available columns: {list(df.columns)}"
            )

    if "date" in col_map:
        df[col_map["date"]] = pd.to_datetime(df[col_map["date"]])
        df = df.sort_values(col_map["date"]).reset_index(drop=True)

    bars: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        bar: dict[str, Any] = {
            "open": float(row[col_map["open"]]),
            "high": float(row[col_map["high"]]),
            "low": float(row[col_map["low"]]),
            "close": float(row[col_map["close"]]),
        }
        if "volume" in col_map:
            bar["volume"] = float(row[col_map["volume"]])
        else:
            bar["volume"] = 0.0
        if "date" in col_map:
            bar["date"] = row[col_map["date"]]
        else:
            bar["date"] = None
        bars.append(bar)

    return DataFeed(bars)


# -- Symbol aliases for common instruments --

SYMBOL_ALIASES: dict[str, str] = {
    "XAUUSD": "GC=F",
    "XAU/USD": "GC=F",
    "GOLD": "GC=F",
    "XAGUSD": "SI=F",
    "XAG/USD": "SI=F",
    "SILVER": "SI=F",
    "BTCUSD": "BTC-USD",
    "BTC/USD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "ETH/USD": "ETH-USD",
    "EURUSD": "EURUSD=X",
    "EUR/USD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USD/JPY": "JPY=X",
    "SPX": "^GSPC",
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DJI": "^DJI",
    "OIL": "CL=F",
    "CRUDE": "CL=F",
}


def resolve_symbol(symbol: str) -> str:
    """Resolve a user-friendly symbol name to a yfinance ticker."""
    upper = symbol.upper().strip()
    return SYMBOL_ALIASES.get(upper, symbol)


def download(
    symbol: str,
    start: str = "2020-01-01",
    end: str | None = None,
    interval: str = "1d",
    output: str | Path | None = None,
) -> DataFeed:
    """Download OHLCV data via yfinance and optionally save to CSV.

    Args:
        symbol: Ticker or alias (e.g. "XAUUSD", "GC=F", "BTC-USD", "AAPL").
        start: Start date string (YYYY-MM-DD).
        end: End date string, or None for today.
        interval: Bar interval — "1m", "5m", "15m", "1h", "1d", "1wk", "1mo".
        output: If provided, save the data to this CSV path.

    Returns:
        DataFeed ready for backtesting.
    """
    import yfinance as yf

    ticker = resolve_symbol(symbol)
    print(f"Downloading {ticker} ({interval}) from {start}...")

    kwargs: dict[str, Any] = {"start": start, "interval": interval}
    if end:
        kwargs["end"] = end

    df = yf.download(ticker, progress=False, **kwargs)

    if df.empty:
        raise ValueError(f"No data returned for symbol '{ticker}'. Check the ticker and date range.")

    # yfinance returns MultiIndex columns when downloading single ticker too sometimes
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()

    # Normalize column names
    rename_map = {}
    for col in df.columns:
        lower = str(col).lower().strip()
        if lower in ("date", "datetime", "index"):
            rename_map[col] = "date"
        elif lower == "open":
            rename_map[col] = "open"
        elif lower == "high":
            rename_map[col] = "high"
        elif lower == "low":
            rename_map[col] = "low"
        elif lower in ("close", "adj close"):
            if "close" not in rename_map.values():
                rename_map[col] = "close"
        elif lower == "volume":
            rename_map[col] = "volume"
    df = df.rename(columns=rename_map)

    if output:
        out_path = Path(output)
        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        df[cols].to_csv(out_path, index=False)
        print(f"Saved {len(df)} bars to {out_path}")

    return load_csv_from_df(df)


def load_csv_from_df(df: pd.DataFrame) -> DataFeed:
    """Build a DataFeed from an already-loaded DataFrame."""
    col_map: dict[str, str] = {}
    for target in ("open", "high", "low", "close", "volume", "date"):
        found = _find_column(df, target)
        if found:
            col_map[target] = found

    for required in ("open", "high", "low", "close"):
        if required not in col_map:
            raise ValueError(
                f"Could not find '{required}' column. Available: {list(df.columns)}"
            )

    if "date" in col_map:
        df[col_map["date"]] = pd.to_datetime(df[col_map["date"]])
        df = df.sort_values(col_map["date"]).reset_index(drop=True)

    bars: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        bar: dict[str, Any] = {
            "open": float(row[col_map["open"]]),
            "high": float(row[col_map["high"]]),
            "low": float(row[col_map["low"]]),
            "close": float(row[col_map["close"]]),
        }
        bar["volume"] = float(row[col_map["volume"]]) if "volume" in col_map else 0.0
        bar["date"] = row[col_map["date"]] if "date" in col_map else None
        bars.append(bar)

    return DataFeed(bars)
