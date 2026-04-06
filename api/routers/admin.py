"""Admin routes — platform admin management."""

import re
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.auth import get_current_admin
from ..models.bot import Bot
from ..models.script import Script
from ..models.user import User
from ..schemas.auth import UserResponse
from ..schemas.bot import BotResponse
from ..schemas.script import ScriptResponse
from ..services.script_service import validate_script

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminUserUpdate(BaseModel):
    max_bots: Optional[int] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class AdminScriptCreate(BaseModel):
    name: str
    source: str
    description: Optional[str] = None


class AdminUserResponse(UserResponse):
    bot_count: int = 0
    account_count: int = 0


@router.get("/users")
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..models.broker_account import BrokerAccount
    from ..models.bot_trade import BotTrade

    result = await db.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    response = []
    for user in users:
        # Bot count and status breakdown
        bot_result = await db.execute(
            select(Bot.status, func.count(Bot.id))
            .where(Bot.user_id == user.id)
            .group_by(Bot.status)
        )
        bot_statuses = {row[0]: row[1] for row in bot_result.all()}
        total_bots = sum(bot_statuses.values())

        # Account count
        acc_result = await db.execute(
            select(func.count(BrokerAccount.id)).where(
                BrokerAccount.user_id == user.id,
                BrokerAccount.is_active == True,
            )
        )
        account_count = acc_result.scalar() or 0

        # Total PnL from trades
        pnl_result = await db.execute(
            select(func.coalesce(func.sum(BotTrade.pnl), 0.0))
            .join(Bot, BotTrade.bot_id == Bot.id)
            .where(Bot.user_id == user.id, BotTrade.pnl.isnot(None))
        )
        total_pnl = float(pnl_result.scalar() or 0)

        response.append({
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "is_email_verified": user.is_email_verified,
            "plan": user.plan,
            "balance": user.balance or 0,
            "max_bots": user.max_bots,
            "created_at": user.created_at.isoformat(),
            "bot_count": total_bots,
            "account_count": account_count,
            "total_pnl": round(total_pnl, 2),
            "bots_running": bot_statuses.get("running", 0),
            "bots_error": bot_statuses.get("error", 0),
            "bots_stopped": bot_statuses.get("stopped", 0),
        })

    return response


@router.get("/bots")
async def list_all_bots(
    request: Request,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Bot)
        .options(selectinload(Bot.user))
        .order_by(Bot.status.desc(), Bot.started_at.desc())
    )
    bots = result.scalars().all()

    return [
        {
            "id": str(b.id),
            "name": b.name,
            "symbol": b.symbol,
            "timeframe": b.timeframe,
            "lot_size": float(b.lot_size),
            "is_live": b.is_live,
            "status": b.status,
            "error_message": b.error_message,
            "started_at": b.started_at.isoformat() if b.started_at else None,
            "stopped_at": b.stopped_at.isoformat() if b.stopped_at else None,
            "user_email": b.user.email if b.user else "unknown",
            "user_name": b.user.full_name if b.user else "unknown",
        }
        for b in bots
    ]


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if body.max_bots is not None:
        user.max_bots = body.max_bots
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin

    await db.flush()
    await db.refresh(user)
    return user


@router.post("/scripts", response_model=ScriptResponse, status_code=status.HTTP_201_CREATED)
async def create_system_script(
    body: AdminScriptCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ok, result = validate_script(body.source)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid Pine Script: {result}")

    filename = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_") + ".pine"

    script = Script(
        user_id=None,
        name=body.name,
        filename=filename,
        source=body.source,
        description=body.description,
        is_system=True,
        is_public=True,
    )
    db.add(script)
    await db.flush()
    await db.refresh(script)
    return script
