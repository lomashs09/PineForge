"""Pydantic schemas for dashboard endpoint."""

from pydantic import BaseModel


class DashboardResponse(BaseModel):
    active_bots: int
    total_bots: int
    broker_accounts: int
    today_pnl: float
    total_pnl: float
    total_trades: int
    win_rate_pct: float
