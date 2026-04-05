"""Direct MT5 executor — executes trades on a SPECIFIC MT5 terminal instance.

Each executor is bound to one MT5 terminal (one broker account).
The MetaTrader5 package is re-initialized with the correct terminal path
before each operation to ensure we're trading on the right account.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger("worker.executor")

_executor_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mt5exec")


class DirectExecutor:
    """Executes trades directly on a specific MT5 terminal instance."""

    def __init__(self, symbol: str, is_live: bool = False, terminal_path: str = ""):
        self._symbol = symbol
        self._is_live = is_live
        self._terminal_path = terminal_path

    def _ensure_connected(self):
        """Re-connect to the correct terminal before each operation."""
        if self._terminal_path:
            import MetaTrader5 as mt5
            if not mt5.initialize(path=self._terminal_path):
                err = mt5.last_error()
                logger.error("Failed to connect to terminal %s: %s", self._terminal_path, err)
                return False
        return True

    async def _run(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor_pool, func, *args)

    async def open_buy(self, volume: float) -> dict[str, Any] | None:
        logger.info("BUY %s %.2f lots of %s", "LIVE" if self._is_live else "DRY", volume, self._symbol)
        if not self._is_live:
            print(f"  [DRY RUN] Would BUY {volume} lots of {self._symbol}", flush=True)
            return {"dry_run": True, "action": "buy", "volume": volume}

        result = await self._run(self._do_market_order, "buy", volume)
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

        result = await self._run(self._do_market_order, "sell", volume)
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

        success, pnl = await self._run(self._do_close_all)
        if success:
            print(f"  [LIVE] Closed all {self._symbol} positions pnl={pnl:.2f}", flush=True)
        else:
            print(f"  [ERROR] Close all failed for {self._symbol}", flush=True)
        return success

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self._is_live:
            return []
        return await self._run(self._do_get_positions)

    async def get_account_info(self) -> dict[str, Any] | None:
        if not self._is_live:
            return {"balance": 100.0, "equity": 100.0, "currency": "USD", "dry_run": True}
        return await self._run(self._do_account_info)

    def _do_market_order(self, order_type: str, volume: float) -> dict:
        import MetaTrader5 as mt5
        self._ensure_connected()
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            return {"success": False, "error": f"Symbol {self._symbol} not found"}
        sym_info = mt5.symbol_info(self._symbol)
        if sym_info and not sym_info.visible:
            mt5.symbol_select(self._symbol, True)
        price = tick.ask if order_type == "buy" else tick.bid
        mt5_type = mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": self._symbol, "volume": volume,
            "type": mt5_type, "price": price, "deviation": 20, "magic": 0,
            "comment": "pineforge", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None:
            return {"success": False, "error": str(mt5.last_error())}
        return {
            "success": result.retcode == mt5.TRADE_RETCODE_DONE,
            "order_id": result.order, "price": result.price, "volume": result.volume,
            "error": "" if result.retcode == mt5.TRADE_RETCODE_DONE else f"retcode={result.retcode}",
        }

    def _do_close_all(self):
        import MetaTrader5 as mt5
        self._ensure_connected()
        positions = mt5.positions_get(symbol=self._symbol)
        if not positions:
            return True, 0.0
        total_pnl = sum(p.profit for p in positions)
        all_ok = True
        for pos in positions:
            tick = mt5.symbol_info_tick(self._symbol)
            if tick is None:
                all_ok = False
                continue
            close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
            price = tick.bid if pos.type == 0 else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": self._symbol, "volume": pos.volume,
                "type": close_type, "position": pos.ticket, "price": price, "deviation": 20,
                "comment": "pineforge-close", "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                all_ok = False
        return all_ok, total_pnl

    def _do_get_positions(self):
        import MetaTrader5 as mt5
        self._ensure_connected()
        positions = mt5.positions_get(symbol=self._symbol)
        if positions is None:
            return []
        return [{"id": str(p.ticket), "type": "POSITION_TYPE_BUY" if p.type == 0 else "POSITION_TYPE_SELL",
                 "symbol": p.symbol, "volume": p.volume, "profit": p.profit, "openPrice": p.price_open}
                for p in positions]

    def _do_account_info(self):
        import MetaTrader5 as mt5
        self._ensure_connected()
        info = mt5.account_info()
        if info is None:
            return None
        return {"balance": info.balance, "equity": info.equity, "currency": info.currency}
