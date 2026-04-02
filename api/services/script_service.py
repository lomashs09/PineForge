"""Script service — validation, seeding, and backtesting."""

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.script import Script

logger = logging.getLogger(__name__)

# Lock to serialize engine.run() calls (it uses module-level singletons)
_backtest_lock = asyncio.Lock()

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


def validate_script(source: str) -> tuple:
    """Parse a Pine Script source and return (ok, strategy_name_or_error).

    Only strategy() scripts are accepted. indicator() scripts are rejected
    because they have no entry/exit logic and cannot be backtested or run as bots.
    """
    try:
        from pineforge.lexer import Lexer
        from pineforge.parser import Parser

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
    loop = asyncio.get_event_loop()

    def _run():
        from pineforge.engine import Engine
        from ..config import get_settings

        settings = get_settings()

        # Use Twelve Data for intraday intervals when date range exceeds yfinance limits
        # yfinance: 5m/15m = 60 days, 1m = 7 days
        # Twelve Data: 1+ year for all intervals
        use_twelvedata = False
        if interval in ("1m", "5m", "15m") and settings.TWELVEDATA_API_KEY:
            from datetime import datetime, timedelta
            yf_limits = {"1m": 7, "5m": 60, "15m": 60}
            max_days = yf_limits.get(interval, 60)
            earliest_yf = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
            if start < earliest_yf:
                use_twelvedata = True

        if use_twelvedata:
            from pineforge.data_twelvedata import download as td_download
            logger.info("Using Twelve Data for %s %s (%s to %s)", symbol, interval, start, end)
            data = td_download(symbol=symbol, start=start, end=end,
                               interval=interval, api_key=settings.TWELVEDATA_API_KEY)
        else:
            from pineforge.data import download
            data = download(symbol=symbol, start=start, end=end, interval=interval)

        engine = Engine(
            initial_capital=capital, fill_on="next_open",
            interval=interval, qty_override=quantity,
        )
        result = engine.run(source, data)
        return result

    async with _backtest_lock:
        result = await loop.run_in_executor(None, _run)

    trades = []
    for t in result.trades:
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

    return {
        "strategy_name": result.strategy_name,
        "total_return_pct": round(result.total_return_pct, 2),
        "total_trades": result.total_trades,
        "win_rate_pct": round(result.win_rate, 2),
        "profit_factor": round(result.profit_factor, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "net_profit": round(result.net_profit, 2),
        "initial_capital": result.initial_capital,
        "final_equity": round(result.final_equity, 2),
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "avg_trade_pnl": round(result.avg_trade_pnl, 2),
        "trades": trades,
    }
