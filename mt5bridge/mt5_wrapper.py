"""Thread-safe wrapper around the MetaTrader5 Python package.

The MetaTrader5 package is synchronous and not thread-safe.
All calls are serialized through a single-threaded executor
so they can be safely called from async FastAPI handlers.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mt5bridge.mt5")

# Single-thread executor — MT5 package is not thread-safe
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")

# Timeframe mapping
TIMEFRAME_MAP: Dict[str, int] = {}

# Will be populated after MT5 import
_mt5 = None


def _ensure_mt5():
    """Lazy-import MetaTrader5 (only available under Wine/Windows)."""
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
            "1w": mt5.TIMEFRAME_W1,
            "1M": mt5.TIMEFRAME_MN1,
        }
    return _mt5


async def _run(func, *args):
    """Run a synchronous MT5 function in the dedicated thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, func, *args)


# ── Connection ────────────────────────────────────────────────────────

def _do_initialize(path: str = "") -> bool:
    mt5 = _ensure_mt5()
    kwargs = {}
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        logger.error("MT5 initialize failed: %s", err)
        return False
    logger.info("MT5 initialized: %s", mt5.terminal_info())
    return True


def _do_login(login: int, password: str, server: str) -> bool:
    mt5 = _ensure_mt5()
    if not mt5.login(login=login, password=password, server=server):
        err = mt5.last_error()
        logger.error("MT5 login failed for %d@%s: %s", login, server, err)
        return False
    info = mt5.account_info()
    logger.info("Logged in: %s (%d) balance=%.2f %s",
                info.name, info.login, info.balance, info.currency)
    return True


def _do_shutdown():
    mt5 = _ensure_mt5()
    mt5.shutdown()
    logger.info("MT5 shutdown")


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


async def shutdown():
    return await _run(_do_shutdown)


async def is_connected() -> bool:
    return await _run(_do_is_connected)


# ── Account Info ──────────────────────────────────────────────────────

def _do_account_info() -> Optional[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "free_margin": info.margin_free,
        "currency": info.currency,
        "leverage": info.leverage,
        "server": info.server,
        "name": info.name,
    }


async def account_info() -> Optional[Dict[str, Any]]:
    return await _run(_do_account_info)


# ── Orders ────────────────────────────────────────────────────────────

def _do_market_order(symbol: str, order_type: str, volume: float,
                     deviation: int, magic: int, comment: str) -> Dict[str, Any]:
    mt5 = _ensure_mt5()

    # Get current price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "retcode": -1,
                "retcode_desc": f"Symbol {symbol} not found or market closed"}

    # Ensure symbol is visible
    if not mt5.symbol_info(symbol).visible:
        mt5.symbol_select(symbol, True)

    price = tick.ask if order_type == "buy" else tick.bid
    mt5_type = mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5_type,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return {"success": False, "retcode": err[0], "retcode_desc": str(err[1])}

    return {
        "success": result.retcode == mt5.TRADE_RETCODE_DONE,
        "order_id": result.order,
        "price": result.price,
        "volume": result.volume,
        "comment": result.comment,
        "retcode": result.retcode,
        "retcode_desc": _retcode_desc(result.retcode),
    }


async def market_buy(symbol: str, volume: float, deviation: int = 20,
                     magic: int = 0, comment: str = "pineforge") -> Dict[str, Any]:
    return await _run(_do_market_order, symbol, "buy", volume, deviation, magic, comment)


async def market_sell(symbol: str, volume: float, deviation: int = 20,
                      magic: int = 0, comment: str = "pineforge") -> Dict[str, Any]:
    return await _run(_do_market_order, symbol, "sell", volume, deviation, magic, comment)


# ── Positions ─────────────────────────────────────────────────────────

def _do_get_positions(symbol: str = "") -> List[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()

    if positions is None:
        return []

    result = []
    for p in positions:
        result.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "buy" if p.type == 0 else "sell",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "profit": p.profit,
            "sl": p.sl,
            "tp": p.tp,
            "magic": p.magic,
            "comment": p.comment,
            "time": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
        })
    return result


def _do_close_positions(symbol: str) -> Tuple[bool, float]:
    """Close all positions for a symbol. Returns (success, total_pnl)."""
    mt5 = _ensure_mt5()
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return True, 0.0

    total_pnl = 0.0
    all_ok = True

    for pos in positions:
        total_pnl += pos.profit

        # Build close request
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
            logger.error("Failed to close position %d: %s", pos.ticket,
                         result.retcode if result else mt5.last_error())
            all_ok = False

    return all_ok, total_pnl


async def get_positions(symbol: str = "") -> List[Dict[str, Any]]:
    return await _run(_do_get_positions, symbol)


async def close_positions(symbol: str) -> Tuple[bool, float]:
    return await _run(_do_close_positions, symbol)


# ── Historical Data ───────────────────────────────────────────────────

def _do_get_candles(symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
    mt5 = _ensure_mt5()
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        logger.error("Unknown timeframe: %s", timeframe)
        return []

    # Ensure symbol is visible
    info = mt5.symbol_info(symbol)
    if info is None:
        return []
    if not info.visible:
        mt5.symbol_select(symbol, True)

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return []

    result = []
    for r in rates:
        result.append({
            "time": datetime.fromtimestamp(r[0], tz=timezone.utc).isoformat(),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": int(r[5]),
        })
    return result


async def get_candles(symbol: str, timeframe: str, count: int = 200) -> List[Dict[str, Any]]:
    return await _run(_do_get_candles, symbol, timeframe, count)


# ── Helpers ───────────────────────────────────────────────────────────

def _retcode_desc(code: int) -> str:
    """Human-readable description for MT5 return codes."""
    descs = {
        10009: "Request completed",
        10013: "Invalid request",
        10014: "Invalid volume",
        10015: "Invalid price",
        10016: "Invalid stops",
        10017: "Trade disabled",
        10018: "Market closed",
        10019: "Insufficient funds",
        10020: "Prices changed",
        10021: "No quotes",
        10024: "Too frequent requests",
        10026: "Autotrading disabled",
        10027: "Modification denied",
        10028: "Too many orders",
    }
    return descs.get(code, f"Unknown retcode {code}")
