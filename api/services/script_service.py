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
    """Parse a Pine Script source and return (ok, strategy_name_or_error)."""
    try:
        from pineforge.lexer import Lexer
        from pineforge.parser import Parser

        tokens = Lexer(source).tokenize()
        Parser(tokens).parse()
        # Extract strategy name from strategy("...") call
        match = re.search(r'strategy\s*\(\s*["\']([^"\']+)["\']', source)
        name = match.group(1) if match else "Unnamed Strategy"
        return True, name
    except Exception as e:
        return False, str(e)


async def seed_system_scripts(db: AsyncSession):
    """Seed system scripts from examples/*.pine files (idempotent)."""
    if not EXAMPLES_DIR.exists():
        logger.warning("Examples directory not found: %s", EXAMPLES_DIR)
        return

    pine_files = sorted(EXAMPLES_DIR.glob("*.pine"))
    seeded = 0

    for path in pine_files:
        filename = path.name
        # Check if already seeded
        result = await db.execute(
            select(Script).where(Script.filename == filename, Script.is_system == True)
        )
        if result.scalar_one_or_none() is not None:
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
) -> dict:
    """Run a backtest using the engine. Returns BacktestResult as dict."""
    loop = asyncio.get_event_loop()

    def _run():
        from pineforge.data import download
        from pineforge.engine import Engine

        data = download(symbol=symbol, start=start, end=end, interval=interval)
        engine = Engine(initial_capital=capital, fill_on="next_open", interval=interval)
        result = engine.run(source, data)
        return result

    async with _backtest_lock:
        result = await loop.run_in_executor(None, _run)

    trades = []
    for t in result.trades:
        trades.append({
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "entry_date": str(t.entry_date) if t.entry_date else None,
            "exit_date": str(t.exit_date) if t.exit_date else None,
        })

    return {
        "strategy_name": result.strategy_name,
        "total_return_pct": round(result.total_return_pct, 2),
        "total_trades": result.total_trades,
        "win_rate_pct": round(result.win_rate * 100, 2),
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
