"""Order executor — places and closes trades via MetaAPI."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("pineforge.live.executor")

TIMEOUT = 30


class Executor:
    """Wraps MetaAPI connection for order execution.

    In dry-run mode, logs orders without executing.
    """

    def __init__(self, connection, symbol: str, is_live: bool = False):
        self._conn = connection
        self._symbol = symbol
        self._is_live = is_live
        self._print_fn = None  # Set by bridge for per-bot output isolation

    def _print(self, *args):
        if self._print_fn:
            self._print_fn(*args)
        else:
            print(*args, flush=True)

    async def open_buy(self, volume: float) -> dict[str, Any] | None:
        """Place a market buy order."""
        logger.info("BUY %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            self._print(f"  [DRY RUN] Would BUY {volume} lots of {self._symbol}")
            return {"dry_run": True, "action": "buy", "volume": volume}
        try:
            result = await asyncio.wait_for(
                self._conn.create_market_buy_order(self._symbol, volume),
                timeout=TIMEOUT,
            )
            logger.info("BUY order filled: %s", result)
            price = result.get('price', result.get('openPrice', ''))
            self._print(f"  [LIVE] BUY {volume} {self._symbol} @ {price} -> order #{result.get('orderId', 'N/A')}")
            return result
        except asyncio.TimeoutError:
            logger.error("BUY order timed out after %ds", TIMEOUT)
            self._print(f"  [ERROR] BUY timed out after {TIMEOUT}s")
            return None
        except Exception as e:
            logger.error("BUY order failed: %s", e)
            self._print(f"  [ERROR] BUY failed: {e}")
            return None

    async def open_sell(self, volume: float) -> dict[str, Any] | None:
        """Place a market sell order."""
        logger.info("SELL %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            self._print(f"  [DRY RUN] Would SELL {volume} lots of {self._symbol}")
            return {"dry_run": True, "action": "sell", "volume": volume}
        try:
            result = await asyncio.wait_for(
                self._conn.create_market_sell_order(self._symbol, volume),
                timeout=TIMEOUT,
            )
            logger.info("SELL order filled: %s", result)
            price = result.get('price', result.get('openPrice', ''))
            self._print(f"  [LIVE] SELL {volume} {self._symbol} @ {price} -> order #{result.get('orderId', 'N/A')}")
            return result
        except asyncio.TimeoutError:
            logger.error("SELL order timed out after %ds", TIMEOUT)
            self._print(f"  [ERROR] SELL timed out after {TIMEOUT}s")
            return None
        except Exception as e:
            logger.error("SELL order failed: %s", e)
            self._print(f"  [ERROR] SELL failed: {e}")
            return None

    async def close_all(self) -> bool:
        """Close all open positions for the symbol."""
        logger.info("CLOSE ALL %s %s", "LIVE" if self._is_live else "DRY", self._symbol)
        if not self._is_live:
            self._print(f"  [DRY RUN] Would CLOSE ALL {self._symbol} positions pnl=0.00")
            return True
        try:
            # Get position profit before closing
            pnl = 0.0
            try:
                positions = await self.get_positions()
                for p in positions:
                    pnl += p.get("profit", 0) or 0
            except Exception:
                pass

            result = await asyncio.wait_for(
                self._conn.close_positions_by_symbol(self._symbol),
                timeout=TIMEOUT,
            )
            logger.info("Close all result: %s", result)
            self._print(f"  [LIVE] Closed all {self._symbol} positions pnl={pnl:.2f}")
            return True
        except asyncio.TimeoutError:
            logger.error("Close all timed out after %ds", TIMEOUT)
            self._print(f"  [ERROR] Close all timed out after {TIMEOUT}s")
            return False
        except Exception as e:
            logger.error("Close all failed: %s", e)
            self._print(f"  [ERROR] Close all failed: {e}")
            return False

    async def close_position(self, position_id: str) -> bool:
        """Close a specific position by ID."""
        logger.info("CLOSE position %s %s", position_id, "LIVE" if self._is_live else "DRY")
        if not self._is_live:
            self._print(f"  [DRY RUN] Would CLOSE position {position_id}")
            return True
        try:
            result = await asyncio.wait_for(
                self._conn.close_position(position_id),
                timeout=TIMEOUT,
            )
            logger.info("Close position result: %s", result)
            self._print(f"  [LIVE] Closed position {position_id}")
            return True
        except asyncio.TimeoutError:
            logger.error("Close position %s timed out after %ds", position_id, TIMEOUT)
            self._print(f"  [ERROR] Close position timed out after {TIMEOUT}s")
            return False
        except Exception as e:
            logger.error("Close position %s failed: %s", position_id, e)
            self._print(f"  [ERROR] Close position failed: {e}")
            return False

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions."""
        if not self._is_live:
            return []
        try:
            positions = await asyncio.wait_for(
                self._conn.get_positions(),
                timeout=TIMEOUT,
            )
            return [p for p in (positions or []) if p.get("symbol") == self._symbol]
        except asyncio.TimeoutError:
            logger.error("Get positions timed out after %ds", TIMEOUT)
            return []
        except Exception as e:
            logger.error("Get positions failed: %s", e)
            return []

    async def get_account_info(self) -> dict[str, Any] | None:
        """Get account balance and equity info."""
        if not self._is_live:
            return {"balance": 100.0, "equity": 100.0, "currency": "USD", "dry_run": True}
        try:
            return await asyncio.wait_for(
                self._conn.get_account_information(),
                timeout=TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("Get account info timed out after %ds", TIMEOUT)
            return None
        except Exception as e:
            logger.error("Get account info failed: %s", e)
            return None
