"""Order executor that uses the MT5 connector abstraction.

Drop-in replacement for executor.py — works with both MetaAPI and
self-hosted bridge backends via the connector interface.
"""

from __future__ import annotations

import logging
from typing import Any

from .connector import MT5Connector

logger = logging.getLogger("pineforge.live.executor")


class ConnectorExecutor:
    """Executes trades via any MT5Connector implementation."""

    def __init__(self, connector: MT5Connector, symbol: str, is_live: bool = False):
        self._conn = connector
        self._symbol = symbol
        self._is_live = is_live

    async def open_buy(self, volume: float) -> dict[str, Any] | None:
        logger.info("BUY %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would BUY {volume} lots of {self._symbol}", flush=True)
            return {"dry_run": True, "action": "buy", "volume": volume}

        result = await self._conn.buy(self._symbol, volume)
        if result is None or not result.success:
            err = result.error if result else "Unknown error"
            logger.error("BUY failed: %s", err)
            print(f"  [ERROR] BUY failed: {err}", flush=True)
            return None

        print(f"  [LIVE] BUY {volume} {self._symbol} @ {result.price} -> order #{result.order_id}", flush=True)
        return {"orderId": result.order_id, "price": result.price, "volume": result.volume}

    async def open_sell(self, volume: float) -> dict[str, Any] | None:
        logger.info("SELL %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would SELL {volume} lots of {self._symbol}", flush=True)
            return {"dry_run": True, "action": "sell", "volume": volume}

        result = await self._conn.sell(self._symbol, volume)
        if result is None or not result.success:
            err = result.error if result else "Unknown error"
            logger.error("SELL failed: %s", err)
            print(f"  [ERROR] SELL failed: {err}", flush=True)
            return None

        print(f"  [LIVE] SELL {volume} {self._symbol} @ {result.price} -> order #{result.order_id}", flush=True)
        return {"orderId": result.order_id, "price": result.price, "volume": result.volume}

    async def close_all(self) -> bool:
        logger.info("CLOSE ALL %s %s", "LIVE" if self._is_live else "DRY", self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would CLOSE ALL {self._symbol} positions pnl=0.00", flush=True)
            return True

        success, pnl = await self._conn.close_all(self._symbol)
        if success:
            print(f"  [LIVE] Closed all {self._symbol} positions pnl={pnl:.2f}", flush=True)
        else:
            print(f"  [ERROR] Close all failed for {self._symbol}", flush=True)
        return success

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self._is_live:
            return []
        positions = await self._conn.get_positions(self._symbol)
        return [
            {
                "id": p.ticket,
                "type": "POSITION_TYPE_BUY" if p.type in ("buy", "POSITION_TYPE_BUY") else "POSITION_TYPE_SELL",
                "symbol": p.symbol,
                "volume": p.volume,
                "profit": p.profit,
                "openPrice": p.price_open,
            }
            for p in positions
        ]

    async def get_account_info(self) -> dict[str, Any] | None:
        if not self._is_live:
            return {"balance": 100.0, "equity": 100.0, "currency": "USD", "dry_run": True}
        info = await self._conn.get_account_info()
        if info is None:
            return None
        return {"balance": info.balance, "equity": info.equity, "currency": info.currency}
