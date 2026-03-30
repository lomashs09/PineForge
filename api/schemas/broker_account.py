"""Pydantic schemas for broker account endpoints."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


class AccountProvisionRequest(BaseModel):
    label: str
    mt5_login: str
    mt5_password: str
    mt5_server: str

    @field_validator("label")
    @classmethod
    def label_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Label is required")
        return v.strip()

    @field_validator("mt5_login")
    @classmethod
    def mt5_login_valid(cls, v):
        if not v or not v.strip():
            raise ValueError("MT5 Login is required")
        if not v.strip().isdigit():
            raise ValueError("MT5 Login must be a number (e.g. 12345678)")
        return v.strip()

    @field_validator("mt5_password")
    @classmethod
    def mt5_password_valid(cls, v):
        if not v or len(v) < 4:
            raise ValueError("MT5 Password must be at least 4 characters")
        return v

    @field_validator("mt5_server")
    @classmethod
    def mt5_server_valid(cls, v):
        if not v or not v.strip():
            raise ValueError("MT5 Server is required")
        return v.strip()


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
