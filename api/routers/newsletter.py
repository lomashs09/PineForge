"""Newsletter subscription endpoint.

Stores subscriber emails in a JSON file (configurable via NEWSLETTER_STORE_PATH
env var, default /var/lib/pineforge/newsletter.json). The file is append-only;
duplicates are deduped on insert. No DB migration is required — when the team
is ready to move to a real subscriber table or to a third-party provider
(Resend/Loops/ConvertKit), swap the storage backend below without touching the
endpoint contract.

Frontend posts to /api/newsletter/subscribe with { email, source? }.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])

_STORE_PATH = Path(os.environ.get("NEWSLETTER_STORE_PATH", "/var/lib/pineforge/newsletter.json"))
_LOCK = asyncio.Lock()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscribeIn(BaseModel):
    email: EmailStr
    source: str | None = Field(default=None, max_length=120)


class SubscribeOut(BaseModel):
    status: str
    already_subscribed: bool = False


def _load() -> list[dict]:
    if not _STORE_PATH.exists():
        return []
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("newsletter store unreadable: %s", e)
    return []


def _save(rows: list[dict]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    tmp.replace(_STORE_PATH)


@router.post("/subscribe", response_model=SubscribeOut, status_code=202)
async def subscribe(payload: SubscribeIn, request: Request) -> SubscribeOut:
    """Add an email to the newsletter list. Idempotent — returns already_subscribed=True
    when the email is already on file. The `source` field captures which page the
    capture came from (e.g. /, /blog/<slug>, footer, /pricing) for attribution."""
    email = payload.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email")

    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)

    async with _LOCK:
        rows = _load()
        existing = next((r for r in rows if r.get("email") == email), None)
        if existing:
            return SubscribeOut(status="ok", already_subscribed=True)

        rows.append({
            "email": email,
            "source": payload.source,
            "ip": ip,
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
        })
        _save(rows)

    logger.info("newsletter subscribe: email=%s source=%s", email, payload.source)
    return SubscribeOut(status="ok", already_subscribed=False)


@router.get("/count")
async def count():
    """Public count of subscribers — useful for "Join 1,200+ traders" social proof."""
    async with _LOCK:
        rows = _load()
    return {"count": len(rows)}
