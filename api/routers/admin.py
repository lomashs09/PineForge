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


@router.get("/users", response_model=List[AdminUserResponse])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User, func.count(Bot.id))
        .outerjoin(Bot, Bot.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    rows = result.all()
    return [
        AdminUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_admin=user.is_admin,
            max_bots=user.max_bots,
            created_at=user.created_at,
            bot_count=count,
        )
        for user, count in rows
    ]


@router.get("/bots", response_model=List[BotResponse])
async def list_all_running_bots(
    request: Request,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.status.in_(["running", "starting"])).order_by(Bot.started_at.desc())
    )
    return result.scalars().all()


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
