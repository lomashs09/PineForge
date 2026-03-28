"""Broker account routes — CRUD + MetaAPI provisioning."""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.broker_account import BrokerAccount
from ..models.bot import Bot
from ..models.user import User
from ..schemas.broker_account import (
    AccountDetailResponse,
    AccountProvisionRequest,
    AccountResponse,
)
from ..services.account_service import (
    get_account_info,
    get_account_positions,
    provision_account,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("/", response_model=List[AccountResponse])
async def list_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.user_id == current_user.id,
            BrokerAccount.is_active == True,
        ).order_by(BrokerAccount.created_at.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountProvisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if not settings.METAAPI_TOKEN:
        raise HTTPException(status_code=500, detail="MetaAPI token not configured")

    try:
        metaapi_account_id = await provision_account(
            metaapi_token=settings.METAAPI_TOKEN,
            mt5_login=body.mt5_login,
            mt5_password=body.mt5_password,
            mt5_server=body.mt5_server,
            label=body.label,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"MetaAPI provisioning failed: {str(e)}")

    account = BrokerAccount(
        user_id=current_user.id,
        label=body.label,
        metaapi_account_id=metaapi_account_id,
        mt5_login=body.mt5_login,
        mt5_server=body.mt5_server,
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountDetailResponse)
async def get_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.id == account_id,
            BrokerAccount.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    # Try to fetch live balance
    settings = get_settings()
    balance_info = {}
    if settings.METAAPI_TOKEN:
        try:
            balance_info = await get_account_info(settings.METAAPI_TOKEN, account.metaapi_account_id)
        except Exception:
            pass  # Return account without live balance if MetaAPI fails

    return AccountDetailResponse(
        id=account.id,
        label=account.label,
        metaapi_account_id=account.metaapi_account_id,
        mt5_login=account.mt5_login,
        mt5_server=account.mt5_server,
        broker_name=account.broker_name,
        is_active=account.is_active,
        created_at=account.created_at,
        **balance_info,
    )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.id == account_id,
            BrokerAccount.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check no running bots use this account
    bot_result = await db.execute(
        select(Bot).where(
            Bot.broker_account_id == account_id,
            Bot.status.in_(["running", "starting"]),
        )
    )
    if bot_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Cannot delete account with running bots")

    # Soft delete
    account.is_active = False


@router.get("/{account_id}/positions")
async def get_positions(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.id == account_id,
            BrokerAccount.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    settings = get_settings()
    if not settings.METAAPI_TOKEN:
        raise HTTPException(status_code=500, detail="MetaAPI token not configured")

    try:
        positions = await get_account_positions(settings.METAAPI_TOKEN, account.metaapi_account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch positions: {str(e)}")

    return positions
