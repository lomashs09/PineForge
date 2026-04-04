"""Bot routes — CRUD + lifecycle management + logs + trades + stats."""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.bot import Bot
from ..models.bot_log import BotLog
from ..models.bot_trade import BotTrade
from ..models.user import User
from ..schemas.bot import (
    BotCreate,
    BotLogResponse,
    BotLogsPage,
    BotResponse,
    BotStatsResponse,
    BotStatusResponse,
    BotTradeResponse,
    BotUpdate,
)
from ..services.bot_service import get_bot_stats, validate_bot_create

router = APIRouter(prefix="/api/bots", tags=["bots"])


def _get_bot_manager(request: Request):
    return request.app.state.bot_manager


@router.get("", response_model=List[BotResponse])
async def list_bots(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user.id).order_by(Bot.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
async def create_bot(
    body: BotCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    error = await validate_bot_create(db, current_user, body.broker_account_id, body.script_id)
    if error:
        raise HTTPException(status_code=400, detail=error)

    bot = Bot(
        user_id=current_user.id,
        broker_account_id=body.broker_account_id,
        script_id=body.script_id,
        name=body.name,
        symbol=body.symbol,
        timeframe=body.timeframe,
        lot_size=body.lot_size,
        max_lot_size=body.max_lot_size,
        max_daily_loss_pct=body.max_daily_loss_pct,
        max_open_positions=body.max_open_positions,
        cooldown_seconds=body.cooldown_seconds,
        poll_interval_seconds=body.poll_interval_seconds,
        lookback_bars=body.lookback_bars,
        is_live=body.is_live,
    )
    db.add(bot)
    await db.flush()
    await db.refresh(bot)
    return bot


@router.get("/{bot_id}", response_model=BotResponse)
async def get_bot(
    bot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


@router.patch("/{bot_id}", response_model=BotResponse)
async def update_bot(
    bot_id: UUID,
    body: BotUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot.status not in ("stopped", "error"):
        raise HTTPException(status_code=400, detail="Bot must be stopped to update config")

    update_data = body.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(bot, key, value)

    await db.flush()
    await db.refresh(bot)
    return bot


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(
    bot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot.status in ("running", "starting"):
        raise HTTPException(status_code=400, detail="Stop the bot before deleting")
    await db.delete(bot)


@router.post("/{bot_id}/start", response_model=BotStatusResponse)
async def start_bot(
    bot_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    from ..config import get_settings as _get_settings
    settings = _get_settings()

    if settings.MT5_BACKEND == "direct":
        # DB-driven: set status to "start_requested", worker picks it up
        if bot.status in ("running", "starting", "start_requested"):
            raise HTTPException(status_code=400, detail=f"Bot is already {bot.status}")
        bot.status = "start_requested"
        bot.error_message = None
        await db.flush()
    else:
        # In-process: start via BotManager (MetaAPI)
        bot_manager = _get_bot_manager(request)
        try:
            await bot_manager.start_bot(bot_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {str(e)}")

    await db.refresh(bot)
    bot_manager = _get_bot_manager(request)
    live_status = bot_manager.get_status(bot_id) or {} if settings.MT5_BACKEND != "direct" else {}

    return BotStatusResponse(
        id=bot.id,
        name=bot.name,
        status=bot.status,
        symbol=bot.symbol,
        timeframe=bot.timeframe,
        lot_size=float(bot.lot_size),
        is_live=bot.is_live,
        started_at=bot.started_at,
        uptime_seconds=live_status.get("uptime_seconds", 0),
        bars_processed=live_status.get("bars_processed", 0),
        last_signal=live_status.get("last_signal"),
    )


@router.post("/{bot_id}/stop", response_model=BotStatusResponse)
async def stop_bot(
    bot_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    from ..config import get_settings as _get_settings
    settings = _get_settings()

    if settings.MT5_BACKEND == "direct":
        # DB-driven: set status to "stop_requested", worker picks it up
        if bot.status in ("running", "starting", "start_requested"):
            bot.status = "stop_requested"
            await db.flush()
        elif bot.status in ("error", "stop_requested"):
            bot.status = "stopped"
            bot.error_message = None
            bot.stopped_at = datetime.now(timezone.utc)
            await db.flush()
    else:
        bot_manager = _get_bot_manager(request)
        await bot_manager.stop_bot(bot_id)

    await db.refresh(bot)
    return BotStatusResponse(
        id=bot.id,
        name=bot.name,
        status=bot.status,
        symbol=bot.symbol,
        timeframe=bot.timeframe,
        lot_size=float(bot.lot_size),
        is_live=bot.is_live,
        started_at=bot.started_at,
        stopped_at=bot.stopped_at,
    )


@router.get("/{bot_id}/logs", response_model=BotLogsPage)
async def get_bot_logs(
    bot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    level: Optional[str] = Query(default=None),
):
    # Verify ownership
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Count total
    count_query = select(func.count(BotLog.id)).where(BotLog.bot_id == bot_id)
    if level:
        count_query = count_query.where(BotLog.level == level)
    total = (await db.execute(count_query)).scalar() or 0

    # Fetch logs
    log_query = (
        select(BotLog)
        .where(BotLog.bot_id == bot_id)
        .order_by(BotLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if level:
        log_query = log_query.where(BotLog.level == level)

    result = await db.execute(log_query)
    logs = result.scalars().all()

    return BotLogsPage(
        total=total,
        logs=[
            BotLogResponse(
                id=log.id,
                level=log.level,
                message=log.message,
                metadata=log.metadata_,
                created_at=log.created_at,
            )
            for log in logs
        ],
    )


@router.get("/{bot_id}/trades", response_model=List[BotTradeResponse])
async def get_bot_trades(
    bot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    # Verify ownership
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    result = await db.execute(
        select(BotTrade)
        .where(BotTrade.bot_id == bot_id)
        .order_by(BotTrade.opened_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{bot_id}/stats", response_model=BotStatsResponse)
async def get_bot_statistics(
    bot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership
    result = await db.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    stats = await get_bot_stats(db, bot_id)
    return stats
