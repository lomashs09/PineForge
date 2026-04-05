"""Pydantic schemas for bot endpoints."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, validator


class BotCreate(BaseModel):
    name: str
    broker_account_id: uuid.UUID
    script_id: uuid.UUID
    symbol: str
    timeframe: str
    lot_size: float
    is_live: bool = False
    max_lot_size: float = 0.1
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    cooldown_seconds: int = 60
    poll_interval_seconds: int = 60
    lookback_bars: int = 200

    @validator("poll_interval_seconds")
    def poll_interval_range(cls, v):
        if v < 10:
            raise ValueError("Poll interval must be at least 10 seconds")
        if v > 3600:
            raise ValueError("Poll interval must be at most 3600 seconds")
        return v


class BotUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    lot_size: Optional[float] = None
    is_live: Optional[bool] = None
    max_lot_size: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_open_positions: Optional[int] = None
    cooldown_seconds: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    lookback_bars: Optional[int] = None

    @validator("poll_interval_seconds")
    def poll_interval_range(cls, v):
        if v is not None:
            if v < 10:
                raise ValueError("Poll interval must be at least 10 seconds")
            if v > 3600:
                raise ValueError("Poll interval must be at most 3600 seconds")
        return v


class BotResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    broker_account_id: uuid.UUID
    script_id: uuid.UUID
    name: str
    symbol: str
    timeframe: str
    lot_size: float
    max_lot_size: float
    max_daily_loss_pct: float
    max_open_positions: int
    cooldown_seconds: int
    poll_interval_seconds: int
    lookback_bars: int
    is_live: bool
    status: str
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BotStatusResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    symbol: str
    timeframe: str
    lot_size: float
    is_live: bool
    started_at: Optional[datetime] = None
    uptime_seconds: int = 0
    bars_processed: int = 0
    last_signal: Optional[str] = None


class BotLogResponse(BaseModel):
    id: int
    level: str
    message: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime


class BotLogsPage(BaseModel):
    total: int
    logs: List[BotLogResponse]


class BotTradeResponse(BaseModel):
    id: uuid.UUID
    direction: str
    symbol: str
    lot_size: float
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    signal: str
    order_id: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BotStatsResponse(BaseModel):
    total_trades: int
    total_pnl: float
    win_rate_pct: float
    avg_trade_pnl: float
    best_trade: float
    worst_trade: float
    winning_trades: int
    losing_trades: int
