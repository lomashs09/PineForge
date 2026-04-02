"""Thread-safe wrapper around MetaTrader5 via mt5linux RPyC bridge.

The MetaTrader5 Python package is Windows-only. In Docker, it runs inside
Wine Python. The `mt5linux` package bridges Linux Python → Wine Python
via RPyC, giving us transparent access to all MT5 functions.

All calls are serialized through a single-threaded executor since MT5
is not thread-safe.
"""

import asyncio
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mt5bridge.mt5")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
_mt5 = None
_rpyc_server = None

# Timeframe constants (will be set from MT5 after connection)
TIMEFRAME_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080, "1M": 43200,
}


def _start_rpyc_server():
    """Start the RPyC server inside Wine Python (background process)."""
    global _rpyc_server
    if _rpyc_server and _rpyc_server.poll() is None:
        return  # Already running

    logger.info("Starting RPyC server in Wine Python...")
    _rpyc_server = subprocess.Popen(
        ["wine", "python", "-c",
         "from rpyc.utils.server import ThreadedServer; "
         "from rpyc import SlaveService; "
         "t = ThreadedServer(SlaveService, port=18812, "
         "protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True}); "
         "t.start()"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for server to be ready
    time.sleep(5)
    logger.info("RPyC server started (pid=%d)", _rpyc_server.pid)


def _get_mt5():
    """Get the MetaTrader5 module via mt5linux RPyC bridge."""
    global _mt5
    if _mt5 is None:
        from mt5linux import MetaTrader5
        _mt5 = MetaTrader5(host="localhost", port=18812)
    return _mt5


async def _run(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, func, *args)


# ── Connection ────────────────────────────────────────────────────────

_DEFAULT_MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

def _do_initialize(path: str = "") -> bool:
    _start_rpyc_server()
    mt5 = _get_mt5()
    # Always pass the path — Wine doesn't set up registry entries
    # so mt5.initialize() can't find the terminal without it
    init_path = path or _DEFAULT_MT5_PATH
    logger.info("Initializing MT5 with path: %s", init_path)
    if not mt5.initialize(path=init_path):
        err = mt5.last_error()
        logger.error("MT5 initialize failed: %s", err)
        return False
    logger.info("MT5 initialized")
    return True


def _do_login(login: int, password: str, server: str) -> bool:
    mt5 = _get_mt5()
    if not mt5.login(login=login, password=password, server=server):
        err = mt5.last_error()
        logger.error("MT5 login failed for %d@%s: %s", login, server, err)
        return False
    info = mt5.account_info()
    if info:
        logger.info("Logged in: %s (%d) balance=%.2f %s",
                     info.name, info.login, info.balance, info.currency)
    return True


def _do_shutdown():
    mt5 = _get_mt5()
    mt5.shutdown()
    logger.info("MT5 shutdown")


def _do_is_connected() -> bool:
    try:
        mt5 = _get_mt5()
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
    mt5 = _get_mt5()
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
    mt5 = _get_mt5()

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "retcode": -1,
                "retcode_desc": f"Symbol {symbol} not found or market closed"}

    sym_info = mt5.symbol_info(symbol)
    if sym_info and not sym_info.visible:
        mt5.symbol_select(symbol, True)

    price = tick.ask if order_type == "buy" else tick.bid

    request = {
        "action": 1,  # TRADE_ACTION_DEAL
        "symbol": symbol,
        "volume": volume,
        "type": 0 if order_type == "buy" else 1,  # ORDER_TYPE_BUY / SELL
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": 0,  # ORDER_TIME_GTC
        "type_filling": 1,  # ORDER_FILLING_IOC
    }

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return {"success": False, "retcode": err[0] if err else -1,
                "retcode_desc": str(err[1]) if err else "Unknown"}

    return {
        "success": result.retcode == 10009,  # TRADE_RETCODE_DONE
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
    mt5 = _get_mt5()
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
    mt5 = _get_mt5()
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return True, 0.0

    total_pnl = 0.0
    all_ok = True

    for pos in positions:
        total_pnl += pos.profit

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            all_ok = False
            continue

        close_type = 1 if pos.type == 0 else 0  # Opposite
        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action": 1,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "pineforge-close",
            "type_time": 0,
            "type_filling": 1,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != 10009:
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
    mt5 = _get_mt5()

    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        logger.error("Unknown timeframe: %s", timeframe)
        return []

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        return []
    if not sym_info.visible:
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
