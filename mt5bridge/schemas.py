"""Request and response models for the MT5 bridge API."""

from typing import List, Optional
from pydantic import BaseModel


# ── Requests ──────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    login: int
    password: str
    server: str


class OrderRequest(BaseModel):
    symbol: str
    volume: float
    deviation: int = 20
    magic: int = 0
    comment: str = "pineforge"


class CloseRequest(BaseModel):
    symbol: str


class CandlesRequest(BaseModel):
    symbol: str
    timeframe: str  # "1m", "5m", "15m", "1h", "4h", "1d"
    count: int = 200


# ── Responses ─────────────────────────────────────────────────────────

class StatusResponse(BaseModel):
    connected: bool
    login: int = 0
    server: str = ""
    terminal_info: Optional[dict] = None


class AccountInfo(BaseModel):
    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    currency: str
    leverage: int
    server: str
    name: str


class OrderResult(BaseModel):
    success: bool
    order_id: int = 0
    price: float = 0.0
    volume: float = 0.0
    comment: str = ""
    retcode: int = 0
    retcode_desc: str = ""


class Position(BaseModel):
    ticket: int
    symbol: str
    type: str  # "buy" or "sell"
    volume: float
    price_open: float
    price_current: float
    profit: float
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 0
    comment: str = ""
    time: str = ""


class Candle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class ErrorResponse(BaseModel):
    error: str
    code: int = 0
