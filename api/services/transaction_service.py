"""Transaction recording helper — creates audit entries for every balance change."""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.transaction import Transaction
from ..models.user import User

logger = logging.getLogger(__name__)


async def record_transaction(
    db: AsyncSession,
    user: User,
    tx_type: str,
    amount: float,
    description: str,
    reference_id: Optional[str] = None,
) -> Transaction:
    """Record a balance change and return the Transaction.

    Args:
        db: Active database session (caller is responsible for commit/flush).
        user: User whose balance was ALREADY updated.
        tx_type: One of 'deposit', 'charge', 'refund', 'manual_credit'.
        amount: Positive for credits, negative for debits.
        description: Human-readable description.
        reference_id: External reference (Stripe session, PayPal order, bot ID, etc.)
    """
    txn = Transaction(
        user_id=user.id,
        type=tx_type,
        amount=round(amount, 4),
        balance_after=round(user.balance or 0, 4),
        description=description,
        reference_id=str(reference_id) if reference_id else None,
    )
    db.add(txn)
    logger.info(
        "Transaction [%s] %s %+.4f → $%.4f | %s | ref=%s",
        tx_type, user.email, amount, user.balance or 0, description, reference_id,
    )
    return txn
