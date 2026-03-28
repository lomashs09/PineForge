"""Pydantic schemas for script endpoints."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ScriptCreate(BaseModel):
    name: str
    source: str
    description: Optional[str] = None


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    source: Optional[str] = None
    description: Optional[str] = None


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
    symbol: str = "XAUUSD"
    interval: str = "1h"
    start: str = "2025-01-06"
    end: str = "2025-12-31"
    capital: float = 10000.0


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
