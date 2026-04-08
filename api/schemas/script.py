"""Pydantic schemas for script endpoints."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ScriptCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source: str = Field(..., min_length=1)
    description: Optional[str] = Field(default=None, max_length=2000)


class ScriptUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    source: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, max_length=2000)


class ScriptSummary(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    name: str
    filename: str
    description: Optional[str] = None
    is_system: bool
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScriptResponse(ScriptSummary):
    source: str


class BacktestRequest(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=20)
    interval: str = Field(default="1h", min_length=1, max_length=5)
    start: str = "2025-01-06"
    end: str = "2025-12-31"
    capital: float = Field(default=10000.0, gt=0, le=10_000_000)
    quantity: Optional[float] = Field(default=None, gt=0, le=10_000)


class TradeResponse(BaseModel):
    direction: str
    entry_price: float
    exit_price: Optional[float] = None
    pnl: float
    pnl_pct: float
    entry_date: Optional[str] = None
    exit_date: Optional[str] = None


class BacktestResponse(BaseModel):
    strategy_name: str
    total_return_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    net_profit: float
    initial_capital: float
    final_equity: float
    winning_trades: int
    losing_trades: int
    avg_trade_pnl: float
    trades: List[TradeResponse]
