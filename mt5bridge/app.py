"""MT5 Bridge — Self-hosted REST API for MetaTrader 5.

Replaces MetaAPI by running MT5 in Docker (Wine) and exposing a REST API
that PineForge can call for order execution, position management, and
historical data.

One bridge instance = one MT5 account. For multiple accounts, run
multiple containers via docker-compose.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import BridgeConfig
from .schemas import (
    AccountInfo,
    Candle,
    CandlesRequest,
    CloseRequest,
    ConnectRequest,
    ErrorResponse,
    OrderRequest,
    OrderResult,
    Position,
    StatusResponse,
)
from . import mt5_wrapper as mt5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("mt5bridge")

config = BridgeConfig.from_env()

# Track connection state
_connected = False
_login = 0
_server = ""
_reconnect_task = None


async def _connect(login: int, password: str, server: str) -> bool:
    """Initialize MT5 terminal and login."""
    global _connected, _login, _server

    ok = await mt5.initialize(config.mt5_path)
    if not ok:
        logger.error("Failed to initialize MT5 terminal")
        return False

    ok = await mt5.login(login, password, server)
    if not ok:
        logger.error("Failed to login to %d@%s", login, server)
        return False

    _connected = True
    _login = login
    _server = server
    logger.info("Connected: %d@%s", login, server)
    return True


async def _auto_reconnect():
    """Background task: reconnect if connection drops."""
    while True:
        await asyncio.sleep(config.reconnect_interval)
        if _connected and not await mt5.is_connected():
            logger.warning("Connection lost, attempting reconnect...")
            ok = await mt5.login(config.mt5_login, config.mt5_password, config.mt5_server)
            if ok:
                logger.info("Reconnected successfully")
            else:
                logger.error("Reconnect failed, will retry in %ds", config.reconnect_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _reconnect_task

    # Auto-connect on startup if credentials are set
    if config.auto_connect and config.mt5_login and config.mt5_password:
        logger.info("Auto-connecting to %d@%s ...", config.mt5_login, config.mt5_server)
        ok = await _connect(config.mt5_login, config.mt5_password, config.mt5_server)
        if not ok:
            logger.warning("Auto-connect failed — use POST /connect to connect manually")

    # Start reconnect watchdog
    _reconnect_task = asyncio.create_task(_auto_reconnect())

    yield

    # Shutdown
    if _reconnect_task:
        _reconnect_task.cancel()
    if _connected:
        await mt5.shutdown()
    logger.info("Bridge shutdown complete")


app = FastAPI(
    title="MT5 Bridge",
    description="Self-hosted REST API for MetaTrader 5. One instance per MT5 account.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_connection():
    if not _connected:
        raise HTTPException(status_code=503, detail="Not connected to MT5. POST /connect first.")


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    connected = _connected and await mt5.is_connected()
    return {"status": "ok", "connected": connected, "login": _login, "server": _server}


@app.post("/connect", response_model=StatusResponse)
async def connect(req: ConnectRequest):
    """Connect to an MT5 account. Initializes the terminal and logs in."""
    global config
    config.mt5_login = req.login
    config.mt5_password = req.password
    config.mt5_server = req.server

    ok = await _connect(req.login, req.password, req.server)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to connect. Check credentials and server name.")

    return StatusResponse(connected=True, login=req.login, server=req.server)


@app.post("/disconnect")
async def disconnect():
    """Disconnect from the MT5 terminal."""
    global _connected, _login, _server
    await mt5.shutdown()
    _connected = False
    _login = 0
    _server = ""
    return {"status": "disconnected"}


@app.get("/account", response_model=AccountInfo)
async def get_account():
    """Get account balance, equity, margin, etc."""
    _require_connection()
    info = await mt5.account_info()
    if info is None:
        raise HTTPException(status_code=500, detail="Failed to get account info")
    return info


@app.post("/order/buy", response_model=OrderResult)
async def order_buy(req: OrderRequest):
    """Place a market buy order."""
    _require_connection()
    result = await mt5.market_buy(req.symbol, req.volume, req.deviation, req.magic, req.comment)
    if not result["success"]:
        logger.warning("BUY failed: %s", result)
    return result


@app.post("/order/sell", response_model=OrderResult)
async def order_sell(req: OrderRequest):
    """Place a market sell order."""
    _require_connection()
    result = await mt5.market_sell(req.symbol, req.volume, req.deviation, req.magic, req.comment)
    if not result["success"]:
        logger.warning("SELL failed: %s", result)
    return result


@app.get("/positions")
async def list_positions(symbol: str = ""):
    """Get open positions, optionally filtered by symbol."""
    _require_connection()
    return await mt5.get_positions(symbol)


@app.post("/positions/close", response_model=OrderResult)
async def close_positions(req: CloseRequest):
    """Close all open positions for a symbol."""
    _require_connection()
    success, pnl = await mt5.close_positions(req.symbol)
    return OrderResult(
        success=success,
        price=pnl,  # Using price field to carry PnL
        comment=f"Closed all {req.symbol} positions, PnL={pnl:.2f}",
    )


@app.post("/candles")
async def get_candles(req: CandlesRequest):
    """Get historical OHLCV candles."""
    _require_connection()
    candles = await mt5.get_candles(req.symbol, req.timeframe, req.count)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No candle data for {req.symbol} {req.timeframe}")
    return candles


# ── Run directly ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.host, port=config.port)
