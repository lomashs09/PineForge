"""Order executor — places and closes trades via MetaAPI."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("strateg.live.executor")


class Executor:
    """Wraps MetaAPI connection for order execution.

    In dry-run mode, logs orders without executing.
    """

    def __init__(self, connection, symbol: str, is_live: bool = False):
        self._conn = connection
        self._symbol = symbol
        self._is_live = is_live

    async def open_buy(self, volume: float) -> dict[str, Any] | None:
        """Place a market buy order."""
        logger.info("BUY %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would BUY {volume} lots of {self._symbol}")
            return {"dry_run": True, "action": "buy", "volume": volume}
        try:
            result = await self._conn.create_market_buy_order(self._symbol, volume)
            logger.info("BUY order filled: %s", result)
            print(f"  [LIVE] BUY {volume} {self._symbol} -> order #{result.get('orderId', 'N/A')}")
            return result
        except Exception as e:
            logger.error("BUY order failed: %s", e)
            print(f"  [ERROR] BUY failed: {e}")
            return None

    async def open_sell(self, volume: float) -> dict[str, Any] | None:
        """Place a market sell order."""
        logger.info("SELL %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would SELL {volume} lots of {self._symbol}")
            return {"dry_run": True, "action": "sell", "volume": volume}
        try:
            result = await self._conn.create_market_sell_order(self._symbol, volume)
            logger.info("SELL order filled: %s", result)
            print(f"  [LIVE] SELL {volume} {self._symbol} -> order #{result.get('orderId', 'N/A')}")
            return result
        except Exception as e:
            logger.error("SELL order failed: %s", e)
            print(f"  [ERROR] SELL failed: {e}")
            return None

    async def close_all(self) -> bool:
        """Close all open positions for the symbol."""
        logger.info("CLOSE ALL %s %s", "LIVE" if self._is_live else "DRY", self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would CLOSE ALL {self._symbol} positions")
            return True
        try:
            result = await self._conn.close_positions_by_symbol(self._symbol)
            logger.info("Close all result: %s", result)
            print(f"  [LIVE] Closed all {self._symbol} positions")
            return True
        except Exception as e:
            logger.error("Close all failed: %s", e)
            print(f"  [ERROR] Close all failed: {e}")
            return False

    async def close_position(self, position_id: str) -> bool:
        """Close a specific position by ID."""
        logger.info("CLOSE position %s %s", position_id, "LIVE" if self._is_live else "DRY")
        if not self._is_live:
            print(f"  [DRY RUN] Would CLOSE position {position_id}")
            return True
        try:
            result = await self._conn.close_position(position_id)
            logger.info("Close position result: %s", result)
            print(f"  [LIVE] Closed position {position_id}")
            return True
        except Exception as e:
            logger.error("Close position %s failed: %s", position_id, e)
            print(f"  [ERROR] Close position failed: {e}")
            return False

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions."""
        if not self._is_live:
            return []
        try:
            positions = await self._conn.get_positions()
            return [p for p in (positions or []) if p.get("symbol") == self._symbol]
        except Exception as e:
            logger.error("Get positions failed: %s", e)
            return []

    async def get_account_info(self) -> dict[str, Any] | None:
        """Get account balance and equity info."""
        if not self._is_live:
            return {"balance": 100.0, "equity": 100.0, "currency": "USD", "dry_run": True}
        try:
            return await self._conn.get_account_information()
        except Exception as e:
            logger.error("Get account info failed: %s", e)
            return None
