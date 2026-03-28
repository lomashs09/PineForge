"""Pydantic schemas for broker account endpoints."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class AccountProvisionRequest(BaseModel):
    label: str
    mt5_login: str
    mt5_password: str
    mt5_server: str


class AccountResponse(BaseModel):
    id: uuid.UUID
    label: str
    metaapi_account_id: str
    mt5_login: str
    mt5_server: str
    broker_name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountDetailResponse(AccountResponse):
    balance: Optional[float] = None
    equity: Optional[float] = None
    currency: Optional[str] = None


class PositionResponse(BaseModel):
    id: str
    type: str
    symbol: str
    volume: float
    openPrice: float
    profit: float
    currentPrice: Optional[float] = None
