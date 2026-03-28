"""Script routes — CRUD + backtest."""

import re
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

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


@router.get("/", response_model=List[ScriptSummary])
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


@router.post("/", response_model=ScriptResponse, status_code=status.HTTP_201_CREATED)
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

    try:
        backtest_result = await run_backtest(
            source=script.source,
            symbol=body.symbol,
            interval=body.interval,
            start=body.start,
            end=body.end,
            capital=body.capital,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Backtest failed: {str(e)}")

    return backtest_result
