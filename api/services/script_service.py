"""Script service — validation, seeding, and backtesting."""

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.script import Script

logger = logging.getLogger(__name__)


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


_MAX_SCRIPT_SIZE = 100 * 1024  # 100KB
_DANGEROUS_PATTERNS = re.compile(
    r'((?:^|[^a-zA-Z_])(?:import|exec|eval|compile)\s*[\(]'
    r'|__import__|__builtins__|__class__|__subclasses__|__globals__|__code__'
    r'|os\s*\.\s*system|subprocess|open\s*\()',
    re.MULTILINE,
)


def validate_script(source: str) -> tuple:
    """Parse a Pine Script source and return (ok, strategy_name_or_error).

    Only strategy() scripts are accepted. indicator() scripts are rejected
    because they have no entry/exit logic and cannot be backtested or run as bots.
    Scripts with dangerous Python patterns are also rejected.
    """
    try:
        from pineforge.lexer import Lexer
        from pineforge.parser import Parser

        # Size check
        if len(source.encode("utf-8")) > _MAX_SCRIPT_SIZE:
            return False, f"Script is too large ({len(source.encode('utf-8'))} bytes). Maximum is {_MAX_SCRIPT_SIZE} bytes."

        # Safety check: reject scripts with Python-specific dangerous patterns
        if _DANGEROUS_PATTERNS.search(source):
            return False, (
                "Script contains disallowed patterns (import, exec, eval, or dunder attributes). "
                "Pine Script does not use these constructs."
            )

        # Check for indicator() — reject early with a clear message
        if re.search(r'\bindicator\s*\(', source):
            return False, (
                "This is an indicator script, not a strategy. "
                "PineForge only supports strategy() scripts that contain "
                "strategy.entry() / strategy.close() calls for backtesting and live trading. "
                "Replace indicator() with strategy() and add entry/exit logic."
            )

        tokens = Lexer(source).tokenize()
        Parser(tokens).parse()

        # Must have a strategy() declaration
        match = re.search(r'strategy\s*\(\s*["\']([^"\']+)["\']', source)
        if not match:
            return False, (
                "No strategy() declaration found. "
                "Scripts must start with strategy(\"Name\", ...) to be used for backtesting and bot trading."
            )

        return True, match.group(1)
    except Exception as e:
        return False, str(e)


async def seed_system_scripts(db: AsyncSession):
    """Seed system scripts from examples/*.pine files (idempotent)."""
    if not EXAMPLES_DIR.exists():
        logger.warning("Examples directory not found: %s", EXAMPLES_DIR)
        return

    pine_files = sorted(EXAMPLES_DIR.glob("*.pine"))
    seeded = 0

    # Fetch all existing system script filenames in one query
    result = await db.execute(
        select(Script.filename).where(Script.is_system == True)
    )
    existing = {row[0] for row in result.all()}

    for path in pine_files:
        filename = path.name
        if filename in existing:
            continue

        source = path.read_text()
        # Extract strategy name
        match = re.search(r'strategy\s*\(\s*["\']([^"\']+)["\']', source)
        if not match:
            continue  # Skip non-strategy files

        name = match.group(1)
        script = Script(
            name=name,
            filename=filename,
            source=source,
            description=f"Built-in strategy: {name}",
            is_system=True,
            is_public=True,
        )
        db.add(script)
        seeded += 1

    if seeded > 0:
        await db.commit()
        logger.info("Seeded %d system scripts", seeded)


async def run_backtest(
    source: str,
    symbol: str,
    interval: str,
    start: str,
    end: str,
    capital: float,
    quantity: float = None,
) -> dict:
    """Run a backtest using the engine. Returns BacktestResult as dict."""

    def _run():
        from pineforge.engine import Engine
        from ..config import get_settings
        from datetime import datetime, timedelta

        settings = get_settings()

        # Fetch warmup data BEFORE start date so indicators are warm at start.
        # 200 bars warmup for indicator stability.
        WARMUP_BARS = 200
        interval_minutes = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440,
        }.get(interval, 60)
        # Forex/gold trades ~5 days/week, so multiply by 1.5 for weekend gaps
        warmup_days = max(1, int((WARMUP_BARS * interval_minutes * 1.5) // 1440) + 1)
        fetch_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=warmup_days)).strftime("%Y-%m-%d")

        # Use Twelve Data for intraday intervals when date range exceeds yfinance limits
        use_twelvedata = False
        if interval in ("1m", "5m", "15m") and settings.TWELVEDATA_API_KEY:
            yf_limits = {"1m": 7, "5m": 60, "15m": 60}
            max_days = yf_limits.get(interval, 60)
            earliest_yf = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
            if fetch_start < earliest_yf:
                use_twelvedata = True

        def _fetch(fetch_start_date: str):
            if use_twelvedata:
                from pineforge.data_twelvedata import download as td_download
                try:
                    return td_download(symbol=symbol, start=fetch_start_date, end=end,
                                       interval=interval, api_key=settings.TWELVEDATA_API_KEY)
                except Exception as td_err:
                    logger.warning("Twelve Data failed for %s: %s — falling back to yfinance", symbol, td_err)
            from pineforge.data import download
            return download(symbol=symbol, start=fetch_start_date, end=end, interval=interval)

        # Try with warmup first; fall back to no-warmup if it fails
        try:
            logger.info("Fetching %s %s with warmup from %s (start=%s, end=%s)",
                        symbol, interval, fetch_start, start, end)
            data = _fetch(fetch_start)
        except Exception as e:
            logger.warning("Warmup fetch failed (%s) — retrying without warmup", e)
            data = _fetch(start)

        if data is None or (hasattr(data, '__len__') and len(data) == 0):
            # Final fallback: try without warmup
            logger.warning("Empty data with warmup, retrying without warmup")
            data = _fetch(start)

        if data is None or (hasattr(data, '__len__') and len(data) == 0):
            raise ValueError(f"No data available for {symbol} ({interval}) from {start} to {end}")

        engine = Engine(
            initial_capital=capital, fill_on="next_open",
            interval=interval, qty_override=quantity,
        )
        result = engine.run(source, data)
        return result

    # Each Engine.run() now creates its own ExecutionContext — safe to run in parallel
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=120,  # 2 minute timeout for backtests
        )
    except asyncio.TimeoutError:
        raise Exception("Backtest timed out after 2 minutes. Try a shorter date range or simpler script.")

    # Filter trades to only those whose entry is within [start, end]
    # (warmup trades before start date are excluded from reported results)
    filtered_trades = []
    for t in result.trades:
        entry_date_str = str(t.entry_date) if t.entry_date else ""
        if entry_date_str and entry_date_str[:10] < start:
            continue  # warmup trade, skip
        filtered_trades.append(t)

    trades = []
    net_profit = 0.0
    winners = 0
    losers = 0
    gross_profit = 0.0
    gross_loss = 0.0
    for t in filtered_trades:
        pnl_pct = (t.pnl / capital * 100) if capital else 0.0
        trades.append({
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "entry_date": str(t.entry_date) if t.entry_date else None,
            "exit_date": str(t.exit_date) if t.exit_date else None,
        })
        net_profit += t.pnl
        if t.pnl > 0:
            winners += 1
            gross_profit += t.pnl
        elif t.pnl < 0:
            losers += 1
            gross_loss += t.pnl

    total_trades = len(filtered_trades)
    win_rate = (winners / total_trades * 100) if total_trades else 0.0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (float("inf") if gross_profit > 0 else 0.0)
    total_return_pct = (net_profit / capital * 100) if capital else 0.0
    avg_trade = net_profit / total_trades if total_trades else 0.0

    return {
        "strategy_name": result.strategy_name,
        "total_return_pct": round(total_return_pct, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "net_profit": round(net_profit, 2),
        "initial_capital": capital,
        "final_equity": round(capital + net_profit, 2),
        "winning_trades": winners,
        "losing_trades": losers,
        "avg_trade_pnl": round(avg_trade, 2),
        "trades": trades,
    }
