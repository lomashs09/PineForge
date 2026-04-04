"""Direct MT5 executor — executes trades via local MT5 terminal.

Drop-in replacement for pineforge/live/executor.py that uses
the MetaTrader5 Python package directly instead of MetaAPI.
"""

import logging
from typing import Any

from . import mt5_direct as mt5

logger = logging.getLogger("worker.executor")


class DirectExecutor:
    """Executes trades directly on the local MT5 terminal."""

    def __init__(self, symbol: str, is_live: bool = False):
        self._symbol = symbol
        self._is_live = is_live

    async def open_buy(self, volume: float) -> dict[str, Any] | None:
        logger.info("BUY %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would BUY {volume} lots of {self._symbol}", flush=True)
            return {"dry_run": True, "action": "buy", "volume": volume}

        result = await mt5.market_buy(self._symbol, volume)
        if not result["success"]:
            print(f"  [ERROR] BUY failed: {result['error']}", flush=True)
            return None

        print(f"  [LIVE] BUY {volume} {self._symbol} @ {result['price']} -> order #{result['order_id']}", flush=True)
        return {"orderId": str(result["order_id"]), "price": result["price"], "volume": result["volume"]}

    async def open_sell(self, volume: float) -> dict[str, Any] | None:
        logger.info("SELL %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would SELL {volume} lots of {self._symbol}", flush=True)
            return {"dry_run": True, "action": "sell", "volume": volume}

        result = await mt5.market_sell(self._symbol, volume)
        if not result["success"]:
            print(f"  [ERROR] SELL failed: {result['error']}", flush=True)
            return None

        print(f"  [LIVE] SELL {volume} {self._symbol} @ {result['price']} -> order #{result['order_id']}", flush=True)
        return {"orderId": str(result["order_id"]), "price": result["price"], "volume": result["volume"]}

    async def close_all(self) -> bool:
        logger.info("CLOSE ALL %s %s", "LIVE" if self._is_live else "DRY", self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would CLOSE ALL {self._symbol} positions pnl=0.00", flush=True)
            return True

        success, pnl = await mt5.close_all(self._symbol)
        if success:
            print(f"  [LIVE] Closed all {self._symbol} positions pnl={pnl:.2f}", flush=True)
        else:
            print(f"  [ERROR] Close all failed for {self._symbol}", flush=True)
        return success

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self._is_live:
            return []
        return await mt5.get_positions(self._symbol)

    async def get_account_info(self) -> dict[str, Any] | None:
        if not self._is_live:
            return {"balance": 100.0, "equity": 100.0, "currency": "USD", "dry_run": True}
        return await mt5.account_info()
