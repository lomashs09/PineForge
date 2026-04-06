"""Bot routes — CRUD + lifecycle management + logs + trades + stats."""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    bots = result.scalars().all()

    # Compute PnL for each bot from trades
    bot_ids = [b.id for b in bots]
    if bot_ids:
        pnl_result = await db.execute(
            select(BotTrade.bot_id, func.coalesce(func.sum(BotTrade.pnl), 0.0))
            .where(BotTrade.bot_id.in_(bot_ids), BotTrade.pnl.isnot(None))
            .group_by(BotTrade.bot_id)
        )
        pnl_map = {row[0]: float(row[1]) for row in pnl_result.all()}
    else:
        pnl_map = {}

    responses = []
    for bot in bots:
        data = BotResponse.model_validate(bot)
        data.pnl = round(pnl_map.get(bot.id, 0.0), 2)
        responses.append(data)
    return responses


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

    pnl_result = await db.execute(
        select(func.coalesce(func.sum(BotTrade.pnl), 0.0))
        .where(BotTrade.bot_id == bot_id, BotTrade.pnl.isnot(None))
    )
    data = BotResponse.model_validate(bot)
    data.pnl = round(float(pnl_result.scalar() or 0), 2)
    return data


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

    # Check balance (minimum $5 to start, admins exempt)
    if not current_user.is_admin and (current_user.balance or 0) < 5.0:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance (${current_user.balance or 0:.2f}). Minimum $5.00 required to start a bot. Add funds in Billing."
        )

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

    # Charge deployment fee ($0.13) — admins exempt
    if not current_user.is_admin:
        current_user.balance = round((current_user.balance or 0) - 0.13, 4)
        await db.flush()

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
        close_result = await bot_manager.stop_bot(bot_id)

    await db.refresh(bot)
    resp = BotStatusResponse(
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
    # Include position close info in response
    if settings.MT5_BACKEND != "direct" and close_result:
        resp.positions_closed = close_result.get("positions_closed", 0)
        resp.close_pnl = close_result.get("pnl", 0.0)
    return resp


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


@router.get("/{bot_id}/positions")
async def get_bot_positions(
    bot_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get open positions for a running bot's symbol."""
    result = await db.execute(
        select(Bot)
        .options(selectinload(Bot.broker_account))
        .where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    from ..config import get_settings as _get_settings
    settings = _get_settings()

    if not settings.METAAPI_TOKEN or settings.MT5_BACKEND == "direct":
        return []

    account = bot.broker_account
    if not account or not account.metaapi_account_id or account.metaapi_account_id.startswith("direct-"):
        return []

    try:
        from ..services.account_service import get_account_positions
        all_positions = await get_account_positions(settings.METAAPI_TOKEN, account.metaapi_account_id)
        # Filter to this bot's symbol
        return [p for p in all_positions if p.get("symbol") == bot.symbol]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch positions: {str(e)}")


@router.get("/{bot_id}/account-info")
async def get_bot_account_info(
    bot_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get broker account info (balance, equity, margin) for a bot's account."""
    result = await db.execute(
        select(Bot)
        .options(selectinload(Bot.broker_account))
        .where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    from ..config import get_settings as _get_settings
    settings = _get_settings()

    if not settings.METAAPI_TOKEN or settings.MT5_BACKEND == "direct":
        return {"balance": 0, "equity": 0, "margin": 0, "freeMargin": 0, "marginLevel": 0, "currency": "USD"}

    account = bot.broker_account
    if not account or not account.metaapi_account_id or account.metaapi_account_id.startswith("direct-"):
        return {"balance": 0, "equity": 0, "margin": 0, "freeMargin": 0, "marginLevel": 0, "currency": "USD"}

    try:
        from ..services.account_service import get_account_info
        return await get_account_info(settings.METAAPI_TOKEN, account.metaapi_account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch account info: {str(e)}")


@router.get("/{bot_id}/history")
async def get_bot_trade_history(
    bot_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get real trade history from MetaAPI for this bot's symbol since bot started."""
    result = await db.execute(
        select(Bot)
        .options(selectinload(Bot.broker_account))
        .where(Bot.id == bot_id, Bot.user_id == current_user.id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    from ..config import get_settings as _get_settings
    settings = _get_settings()

    if not settings.METAAPI_TOKEN or settings.MT5_BACKEND == "direct":
        return []

    account = bot.broker_account
    if not account or not account.metaapi_account_id or account.metaapi_account_id.startswith("direct-"):
        return []

    # Get deals since bot started (or last 24h if no start time)
    from datetime import datetime as dt, timezone as tz, timedelta
    start = bot.started_at
    if start and isinstance(start, str):
        start = dt.fromisoformat(start)
    if not start:
        start = dt.now(tz.utc) - timedelta(hours=24)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz.utc)
    end = dt.now(tz.utc)

    try:
        from ..services.account_service import get_history_deals
        deals = await get_history_deals(
            settings.METAAPI_TOKEN,
            account.metaapi_account_id,
            start,
            end,
            symbol=bot.symbol,
        )

        # Pair entry/exit deals by position ID to show complete trades
        # Entry deals: entryType="in", profit=0
        # Exit deals: entryType="out", profit=actual P&L
        entries = {}  # positionId -> deal
        trades = []

        for d in deals:
            pos_id = d.get("positionId", "")
            entry_type = d.get("entryType", "")

            if entry_type == "DEAL_ENTRY_IN":
                entries[pos_id] = d
            elif entry_type == "DEAL_ENTRY_OUT":
                entry = entries.get(pos_id, {})
                trades.append({
                    "time": d.get("time", ""),
                    "type": "buy" if d.get("type") == "DEAL_TYPE_BUY" else "sell",
                    "symbol": d.get("symbol", ""),
                    "volume": d.get("volume", 0),
                    "entryPrice": entry.get("price", 0),
                    "closePrice": d.get("price", 0),
                    "profit": d.get("profit", 0),
                    "commission": d.get("commission", 0),
                    "swap": d.get("swap", 0),
                    "orderId": str(d.get("orderId", "")),
                    "positionId": str(pos_id),
                    "dealId": str(d.get("id", "")),
                })

        # Sort by time descending
        trades.sort(key=lambda t: t["time"], reverse=True)
        return trades

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch trade history: {str(e)}")
