"""Public, unauthenticated platform stats — for landing page social proof.

Cached for 5 minutes per process to avoid hammering the DB. The numbers shown
are intentionally aggregate and anonymous; nothing here exposes user identity.
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.bot import Bot
from ..models.bot_trade import BotTrade
from ..models.user import User

router = APIRouter(prefix="/api/public", tags=["public"])

_CACHE: dict = {"data": None, "ts": 0.0}
_CACHE_TTL_SEC = 300  # 5 minutes
_CACHE_LOCK = asyncio.Lock()


@router.get("/stats")
async def public_stats(db: AsyncSession = Depends(get_db)):
    """Returns aggregate platform stats. Safe to call from anonymous frontend.

    Response:
      {
        "users": 1234,           // total verified users
        "bots_total": 567,       // bots ever created
        "bots_active": 89,       // currently running
        "trades_30d": 12345,     // trades closed in the last 30 days
        "as_of": "2026-04-27T..."
      }
    """
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE_TTL_SEC:
            return _CACHE["data"]

        # Email-verified users (only count fully onboarded accounts).
        users_q = await db.execute(select(func.count(User.id)).where(User.is_email_verified.is_(True)))
        users = users_q.scalar() or 0

        # Bot counts
        bots_total_q = await db.execute(select(func.count(Bot.id)))
        bots_total = bots_total_q.scalar() or 0

        bots_active_q = await db.execute(
            select(func.count(Bot.id)).where(Bot.status == "running")
        )
        bots_active = bots_active_q.scalar() or 0

        # Trades closed in the last 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        trades_q = await db.execute(
            select(func.count(BotTrade.id)).where(BotTrade.closed_at >= cutoff)
        )
        trades_30d = trades_q.scalar() or 0

        data = {
            "users": users,
            "bots_total": bots_total,
            "bots_active": bots_active,
            "trades_30d": trades_30d,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        _CACHE["data"] = data
        _CACHE["ts"] = now
        return data
