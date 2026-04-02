"""Script routes — CRUD + backtest."""

import re
from datetime import datetime, timedelta
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.script import Script
from ..models.user import User
from ..schemas.script import (
    BacktestRequest,
    BacktestResponse,
    ScriptCreate,
    ScriptResponse,
    ScriptSummary,
    ScriptUpdate,
)
from ..services.script_service import run_backtest, validate_script

# yfinance max lookback days per interval
_INTERVAL_MAX_DAYS = {
    "1d": 5 * 365,
    "1h": 365 * 2,
    "15m": 60,
    "5m": 60,
    "1m": 7,
}

_SYMBOLS = [
    {"symbol": "XAUUSD", "name": "Gold", "category": "commodity"},
    {"symbol": "XAGUSD", "name": "Silver", "category": "commodity"},
    {"symbol": "EURUSD", "name": "EUR/USD", "category": "forex"},
    {"symbol": "GBPUSD", "name": "GBP/USD", "category": "forex"},
    {"symbol": "USDJPY", "name": "USD/JPY", "category": "forex"},
    {"symbol": "BTCUSD", "name": "Bitcoin", "category": "crypto"},
    {"symbol": "ETHUSD", "name": "Ethereum", "category": "crypto"},
    {"symbol": "AAPL", "name": "Apple", "category": "stock"},
    {"symbol": "SPY", "name": "S&P 500 ETF", "category": "stock"},
    {"symbol": "OIL", "name": "Crude Oil", "category": "commodity"},
]

_INTERVALS = [
    {"value": "1d", "label": "1 Day", "max_days": _INTERVAL_MAX_DAYS["1d"]},
    {"value": "1h", "label": "1 Hour", "max_days": _INTERVAL_MAX_DAYS["1h"]},
    {"value": "15m", "label": "15 Minutes", "max_days": _INTERVAL_MAX_DAYS["15m"]},
    {"value": "5m", "label": "5 Minutes", "max_days": _INTERVAL_MAX_DAYS["5m"]},
    {"value": "1m", "label": "1 Minute", "max_days": _INTERVAL_MAX_DAYS["1m"]},
]

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


@router.get("/backtest/config")
async def get_backtest_config():
    """Return available symbols, intervals, and date range limits."""
    from ..config import get_settings
    settings = get_settings()
    has_twelvedata = bool(settings.TWELVEDATA_API_KEY)

    # With Twelve Data, intraday limits are much higher
    intervals = []
    for i in _INTERVALS:
        if has_twelvedata and i["value"] in ("1m", "5m", "15m"):
            intervals.append({**i, "max_days": 365})  # 1 year with Twelve Data
        else:
            intervals.append(i)

    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "symbols": _SYMBOLS,
        "intervals": intervals,
        "today": today,
    }


@router.get("", response_model=List[ScriptSummary])
async def list_scripts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List system scripts + current user's scripts."""
    result = await db.execute(
        select(Script).where(
            or_(Script.is_system == True, Script.user_id == current_user.id)
        ).order_by(Script.is_system.desc(), Script.name)
    )
    return result.scalars().all()


@router.get("/{script_id}", response_model=ScriptResponse)
async def get_script(
    script_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    if not script.is_system and script.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return script


@router.post("", response_model=ScriptResponse, status_code=status.HTTP_201_CREATED)
async def create_script(
    body: ScriptCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ok, result = validate_script(body.source)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid Pine Script: {result}")

    # Generate filename from name
    filename = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_") + ".pine"

    script = Script(
        user_id=current_user.id,
        name=body.name,
        filename=filename,
        source=body.source,
        description=body.description,
    )
    db.add(script)
    await db.flush()
    await db.refresh(script)
    return script


@router.put("/{script_id}", response_model=ScriptResponse)
async def update_script(
    script_id: UUID,
    body: ScriptUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    if script.is_system:
        raise HTTPException(status_code=403, detail="Cannot modify system scripts")
    if script.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if body.source is not None:
        ok, err = validate_script(body.source)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Invalid Pine Script: {err}")
        script.source = body.source

    if body.name is not None:
        script.name = body.name
        script.filename = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_") + ".pine"

    if body.description is not None:
        script.description = body.description

    await db.flush()
    await db.refresh(script)
    return script


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(
    script_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    if script.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete system scripts")
    if script.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    await db.delete(script)


@router.post("/{script_id}/backtest", response_model=BacktestResponse)
async def backtest_script(
    script_id: UUID,
    body: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    if not script.is_system and script.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Enforce date range limits based on interval
    # With Twelve Data, intraday limits are expanded to 365 days
    from ..config import get_settings as _get_settings
    _settings = _get_settings()
    max_days = _INTERVAL_MAX_DAYS.get(body.interval, 365)
    if _settings.TWELVEDATA_API_KEY and body.interval in ("1m", "5m", "15m"):
        max_days = 365
    earliest = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
    start = body.start
    if start < earliest:
        start = earliest

    end = body.end
    today = datetime.now().strftime("%Y-%m-%d")
    if end > today:
        end = today

    try:
        backtest_result = await run_backtest(
            source=script.source,
            symbol=body.symbol,
            interval=body.interval,
            start=start,
            end=end,
            capital=body.capital,
            quantity=body.quantity,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Backtest failed: {str(e)}")

    return backtest_result
