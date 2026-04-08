"""Pydantic schemas for bot endpoints."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class BotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    broker_account_id: uuid.UUID
    script_id: uuid.UUID
    symbol: str = Field(..., min_length=1, max_length=20)
    timeframe: str = Field(..., min_length=1, max_length=10)
    lot_size: float = Field(..., gt=0, le=100, description="Trade lot size (must be > 0)")
    is_live: bool = False
    max_lot_size: float = Field(default=0.1, gt=0, le=100)
    max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100)
    max_open_positions: int = Field(default=1, ge=1, le=50)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    poll_interval_seconds: int = Field(default=60, ge=10, le=3600)
    lookback_bars: int = Field(default=200, ge=10, le=5000)

    @field_validator("lot_size")
    @classmethod
    def lot_size_lte_max(cls, v, info):
        max_lot = info.data.get("max_lot_size", 0.1)
        if v > max_lot:
            raise ValueError(f"lot_size ({v}) cannot exceed max_lot_size ({max_lot})")
        return v


class BotUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    symbol: Optional[str] = Field(default=None, min_length=1, max_length=20)
    timeframe: Optional[str] = Field(default=None, min_length=1, max_length=10)
    lot_size: Optional[float] = Field(default=None, gt=0, le=100)
    is_live: Optional[bool] = None
    max_lot_size: Optional[float] = Field(default=None, gt=0, le=100)
    max_daily_loss_pct: Optional[float] = Field(default=None, gt=0, le=100)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=50)
    cooldown_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    poll_interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    lookback_bars: Optional[int] = Field(default=None, ge=10, le=5000)


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
    script_name: Optional[str] = None
    pnl: float = 0.0
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
    stopped_at: Optional[datetime] = None
    uptime_seconds: int = 0
    bars_processed: int = 0
    last_signal: Optional[str] = None
    positions_closed: int = 0
    close_pnl: float = 0.0


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
