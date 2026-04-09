"""Configuration for the live trading bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class LiveConfig:
    # MetaAPI credentials
    metaapi_token: str = ""
    metaapi_account_id: str = ""

    # Trading parameters
    symbol: str = "XAUUSDm"
    timeframe: str = "1h"
    lot_size: float = 0.01
    max_lot_size: float = 0.1

    # Risk management
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    cooldown_seconds: int = 60

    # Execution
    is_live: bool = False
    poll_interval_seconds: int = 60
    lookback_bars: int = 200

    # Strategy
    script_path: str = ""

    def validate(self) -> list[str]:
        errors = []
        if not self.metaapi_token:
            errors.append("METAAPI_TOKEN is not set")
        if not self.metaapi_account_id:
            errors.append("METAAPI_ACCOUNT_ID is not set")
        if not self.script_path:
            errors.append("--script is required")
        if self.lot_size <= 0:
            errors.append("Lot size must be positive")
        if self.lot_size > self.max_lot_size:
            errors.append(f"Lot size {self.lot_size} exceeds max {self.max_lot_size}")
        return errors


def load_config(**overrides) -> LiveConfig:
    """Load config from .env file and apply CLI overrides."""
    env_paths = [Path(".env"), Path(__file__).resolve().parent.parent.parent / ".env"]
    for p in env_paths:
        if p.exists():
            load_dotenv(p)
            break

    cfg = LiveConfig(
        metaapi_token=os.getenv("METAAPI_TOKEN", ""),
        metaapi_account_id=os.getenv("METAAPI_ACCOUNT_ID", ""),
    )

    for key, val in overrides.items():
        if val is not None and hasattr(cfg, key):
            setattr(cfg, key, val)

    return cfg
