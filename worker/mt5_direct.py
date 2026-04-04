"""Direct MT5 terminal access — no MetaAPI, no Wine.

Runs on Windows alongside the MT5 terminal. Uses the official MetaTrader5
Python package which communicates with MT5 via IPC named pipe.

This replaces MetaAPI entirely when running on a Windows machine.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("worker.mt5")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
_mt5 = None
_initialized = False

TIMEFRAME_MAP = {}


def _ensure_mt5():
    global _mt5, TIMEFRAME_MAP
    if _mt5 is None:
        import MetaTrader5 as mt5
        _mt5 = mt5
        TIMEFRAME_MAP = {
            "1m": mt5.TIMEFRAME_M1,
            "5m": mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,
            "4h": mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
        }
    return _mt5


async def _run(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, func, *args)


# ── Initialize & Login ────────────────────────────────────────────────

def _do_initialize(path: str = "") -> bool:
    global _initialized
    mt5 = _ensure_mt5()
    kwargs = {}
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        logger.error("MT5 initialize failed: %s", err)
        return False
    _initialized = True
    info = mt5.terminal_info()
    logger.info("MT5 initialized: %s (build %s)", info.name, info.build)
    return True


def _do_login(login: int, password: str, server: str) -> bool:
    mt5 = _ensure_mt5()
    if not mt5.login(login=login, password=password, server=server):
        err = mt5.last_error()
        logger.error("Login failed for %d@%s: %s", login, server, err)
        return False
    info = mt5.account_info()
    logger.info("Logged in: %s (%d) balance=%.2f %s",
                info.name, info.login, info.balance, info.currency)
    return True


def _do_is_connected() -> bool:
    mt5 = _ensure_mt5()
    try:
        info = mt5.terminal_info()
        return info is not None and info.connected
    except Exception:
        return False


async def initialize(path: str = "") -> bool:
    return await _run(_do_initialize, path)


async def login(login: int, password: str, server: str) -> bool:
    return await _run(_do_login, login, password, server)


async def is_connected() -> bool:
    return await _run(_do_is_connected)


async def shutdown():
    mt5 = _ensure_mt5()
    return await _run(mt5.shutdown)


# ── Account Info ──────────────────────────────────────────────────────

def _do_account_info() -> Optional[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "balance": info.balance,
        "equity": info.equity,
        "currency": info.currency,
        "login": info.login,
        "server": info.server,
        "name": info.name,
    }


async def account_info() -> Optional[Dict[str, Any]]:
    return await _run(_do_account_info)


# ── Orders ────────────────────────────────────────────────────────────

def _do_market_order(symbol: str, order_type: str, volume: float) -> Dict[str, Any]:
    mt5 = _ensure_mt5()

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "error": f"Symbol {symbol} not found or market closed"}

    sym_info = mt5.symbol_info(symbol)
    if sym_info and not sym_info.visible:
        mt5.symbol_select(symbol, True)

    price = tick.ask if order_type == "buy" else tick.bid
    mt5_type = mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5_type,
        "price": price,
        "deviation": 20,
        "magic": 0,
        "comment": "pineforge",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return {"success": False, "error": str(err)}

    return {
        "success": result.retcode == mt5.TRADE_RETCODE_DONE,
        "order_id": result.order,
        "price": result.price,
        "volume": result.volume,
        "retcode": result.retcode,
        "error": "" if result.retcode == mt5.TRADE_RETCODE_DONE else f"retcode={result.retcode}",
    }


async def market_buy(symbol: str, volume: float) -> Dict[str, Any]:
    return await _run(_do_market_order, symbol, "buy", volume)


async def market_sell(symbol: str, volume: float) -> Dict[str, Any]:
    return await _run(_do_market_order, symbol, "sell", volume)


# ── Positions ─────────────────────────────────────────────────────────

def _do_get_positions(symbol: str = "") -> List[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []
    return [
        {
            "id": str(p.ticket),
            "type": "POSITION_TYPE_BUY" if p.type == 0 else "POSITION_TYPE_SELL",
            "symbol": p.symbol,
            "volume": p.volume,
            "profit": p.profit,
            "openPrice": p.price_open,
        }
        for p in positions
    ]


def _do_close_all(symbol: str) -> Tuple[bool, float]:
    mt5 = _ensure_mt5()
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return True, 0.0

    total_pnl = sum(p.profit for p in positions)
    all_ok = True

    for pos in positions:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            all_ok = False
            continue

        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "pineforge-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            all_ok = False

    return all_ok, total_pnl


async def get_positions(symbol: str = "") -> List[Dict[str, Any]]:
    return await _run(_do_get_positions, symbol)


async def close_all(symbol: str) -> Tuple[bool, float]:
    return await _run(_do_close_all, symbol)


# ── Candles ───────────────────────────────────────────────────────────

def _do_get_candles(symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        return []

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        return []
    if not sym_info.visible:
        mt5.symbol_select(symbol, True)

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return []

    return [
        {
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": int(r[5]),
            "date": datetime.fromtimestamp(r[0], tz=timezone.utc).isoformat(),
        }
        for r in rates
    ]


async def get_candles(symbol: str, timeframe: str, count: int = 200) -> List[Dict[str, Any]]:
    return await _run(_do_get_candles, symbol, timeframe, count)
