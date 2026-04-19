"""Tests for the transaction audit trail system."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from api.models.transaction import Transaction
from api.models.user import User
from api.services.transaction_service import record_transaction


# ── Helpers ───────────────────────────────────────────────────────


def _make_user(balance=100.0):
    """Create a mock User object for testing."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.balance = balance
    user.is_admin = False
    return user


def _make_db():
    """Create a mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


# ── record_transaction unit tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_record_transaction_deposit():
    """Deposits should record positive amount and correct balance_after."""
    user = _make_user(balance=110.0)
    db = _make_db()

    txn = await record_transaction(
        db, user, "deposit", 10.0,
        "Stripe ₹100 → $10.0000",
        reference_id="cs_test_abc123",
    )

    assert isinstance(txn, Transaction)
    assert txn.type == "deposit"
    assert txn.amount == 10.0
    assert txn.balance_after == 110.0
    assert txn.description == "Stripe ₹100 → $10.0000"
    assert txn.reference_id == "cs_test_abc123"
    assert txn.user_id == user.id
    db.add.assert_called_once_with(txn)


@pytest.mark.asyncio
async def test_record_transaction_charge():
    """Charges should record negative amount."""
    user = _make_user(balance=96.848)
    db = _make_db()

    txn = await record_transaction(
        db, user, "charge", -3.0,
        "MT5 account setup: Demo (12345@MetaQuotes)",
    )

    assert txn.type == "charge"
    assert txn.amount == -3.0
    assert txn.balance_after == 96.848
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_record_transaction_refund():
    """Refunds should record positive amount with refund type."""
    user = _make_user(balance=100.152)
    db = _make_db()

    bot_id = uuid.uuid4()
    txn = await record_transaction(
        db, user, "refund", 0.152,
        "Bot start failed (refund): MyBot — connection timeout",
        reference_id=str(bot_id),
    )

    assert txn.type == "refund"
    assert txn.amount == 0.152
    assert txn.balance_after == 100.152
    assert txn.reference_id == str(bot_id)


@pytest.mark.asyncio
async def test_record_transaction_manual_credit():
    """Manual credits should record correctly."""
    user = _make_user(balance=11.1772)
    db = _make_db()

    txn = await record_transaction(
        db, user, "manual_credit", 10.0,
        "Manual credit by admin",
    )

    assert txn.type == "manual_credit"
    assert txn.amount == 10.0
    assert txn.balance_after == 11.1772


@pytest.mark.asyncio
async def test_record_transaction_rounds_amount():
    """Amounts should be rounded to 4 decimal places."""
    user = _make_user(balance=99.99999)
    db = _make_db()

    txn = await record_transaction(
        db, user, "charge", -0.001833333,
        "Usage billing (1 bot $0.0018)",
    )

    assert txn.amount == -0.0018  # rounded to 4 places
    assert txn.balance_after == 100.0  # rounded to 4 places


@pytest.mark.asyncio
async def test_record_transaction_no_reference():
    """Reference ID should be None when not provided."""
    user = _make_user(balance=50.0)
    db = _make_db()

    txn = await record_transaction(
        db, user, "charge", -3.0,
        "MT5 account setup",
    )

    assert txn.reference_id is None


@pytest.mark.asyncio
async def test_record_transaction_uuid_reference():
    """UUID reference IDs should be converted to string."""
    user = _make_user(balance=50.0)
    db = _make_db()
    ref = uuid.uuid4()

    txn = await record_transaction(
        db, user, "charge", -0.152,
        "Bot start",
        reference_id=ref,
    )

    assert txn.reference_id == str(ref)


@pytest.mark.asyncio
async def test_record_transaction_zero_balance():
    """Should handle zero balance correctly."""
    user = _make_user(balance=0.0)
    db = _make_db()

    txn = await record_transaction(
        db, user, "deposit", 5.0,
        "First deposit",
    )

    assert txn.balance_after == 0.0  # balance_after reflects user.balance at call time


@pytest.mark.asyncio
async def test_record_transaction_none_balance():
    """Should handle None balance (new user edge case)."""
    user = _make_user(balance=None)
    db = _make_db()

    txn = await record_transaction(
        db, user, "deposit", 5.0,
        "First deposit",
    )

    assert txn.balance_after == 0.0  # None coerced to 0


# ── Transaction model tests ───────────────────────────────────────


def test_transaction_model_fields():
    """Transaction model should have all required fields."""
    txn = Transaction(
        user_id=uuid.uuid4(),
        type="deposit",
        amount=10.0,
        balance_after=110.0,
        description="Test deposit",
        reference_id="ref_123",
    )

    assert txn.type == "deposit"
    assert txn.amount == 10.0
    assert txn.balance_after == 110.0
    assert txn.description == "Test deposit"
    assert txn.reference_id == "ref_123"


def test_transaction_model_optional_reference():
    """reference_id should be optional."""
    txn = Transaction(
        user_id=uuid.uuid4(),
        type="charge",
        amount=-3.0,
        balance_after=97.0,
        description="Account setup",
    )

    assert txn.reference_id is None


def test_transaction_tablename():
    """Table name should be 'transactions'."""
    assert Transaction.__tablename__ == "transactions"


# ── Usage billing integration tests ──────────────────────────────


@pytest.mark.asyncio
async def test_billing_charge_description_single_bot():
    """Usage billing description should show bot count and cost."""
    user = _make_user(balance=99.998)
    db = _make_db()

    # Simulate what usage_billing.py does
    bot_cost = 0.022 * (300 / 3600)  # 5 min interval
    account_cost = 0.002 * (300 / 3600)
    total_cost = bot_cost + account_cost

    parts = []
    parts.append(f"1 bot ${bot_cost:.4f}")
    parts.append(f"1 acct ${account_cost:.4f}")
    desc = f"Usage billing ({', '.join(parts)})"

    txn = await record_transaction(db, user, "charge", -total_cost, desc)

    assert "Usage billing" in txn.description
    assert "1 bot" in txn.description
    assert "1 acct" in txn.description
    assert txn.type == "charge"
    assert txn.amount < 0


@pytest.mark.asyncio
async def test_billing_charge_description_multiple_bots():
    """Usage billing should pluralize correctly for multiple bots."""
    user = _make_user(balance=99.0)
    db = _make_db()

    billable_bots = 3
    active_accounts = 2
    bot_cost = billable_bots * 0.022 * (300 / 3600)
    account_cost = active_accounts * 0.002 * (300 / 3600)

    parts = []
    parts.append(f"{billable_bots} bots ${bot_cost:.4f}")
    parts.append(f"{active_accounts} accts ${account_cost:.4f}")
    desc = f"Usage billing ({', '.join(parts)})"

    txn = await record_transaction(db, user, "charge", -(bot_cost + account_cost), desc)

    assert "3 bots" in txn.description
    assert "2 accts" in txn.description


# ── Deposit description tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_stripe_inr_deposit_description():
    """Stripe INR deposit should show ₹ amount and USD equivalent."""
    user = _make_user(balance=11.18)
    db = _make_db()

    paid_amount = 100.0
    usd_credit = 1.18
    currency_sym = '₹'

    txn = await record_transaction(
        db, user, "deposit", usd_credit,
        f"Stripe {currency_sym}{paid_amount:.2f} → ${usd_credit:.4f}",
        reference_id="cs_test_xyz",
    )

    assert "₹100.00" in txn.description
    assert "$1.1800" in txn.description
    assert txn.amount == 1.18


@pytest.mark.asyncio
async def test_paypal_usd_deposit_description():
    """PayPal USD deposit should show dollar amount."""
    user = _make_user(balance=60.0)
    db = _make_db()

    txn = await record_transaction(
        db, user, "deposit", 50.0,
        "PayPal $50.00",
        reference_id="PAYPAL-ORDER-123",
    )

    assert "PayPal $50.00" in txn.description
    assert txn.amount == 50.0


# ── Bot charge/refund description tests ──────────────────────────


@pytest.mark.asyncio
async def test_bot_deployment_charge_description():
    """Bot start charge should include bot name and details."""
    user = _make_user(balance=9.848)
    db = _make_db()
    bot_id = uuid.uuid4()

    txn = await record_transaction(
        db, user, "charge", -0.152,
        f"Bot start: XAUUSD Scalper (XAUUSD 5m) — deploy + 1hr prepaid",
        reference_id=str(bot_id),
    )

    assert "Bot start" in txn.description
    assert "XAUUSD" in txn.description
    assert "deploy + 1hr prepaid" in txn.description
    assert txn.amount == -0.152


@pytest.mark.asyncio
async def test_bot_refund_description():
    """Bot failure refund should include error reason."""
    user = _make_user(balance=10.152)
    db = _make_db()
    bot_id = uuid.uuid4()

    txn = await record_transaction(
        db, user, "refund", 0.152,
        f"Bot start failed (refund): MyBot — MetaAPI connection timeout",
        reference_id=str(bot_id),
    )

    assert "refund" in txn.description
    assert "MyBot" in txn.description
    assert txn.amount == 0.152
    assert txn.type == "refund"


# ── Account setup charge test ────────────────────────────────────


@pytest.mark.asyncio
async def test_account_setup_charge_description():
    """Account setup charge should include account details."""
    user = _make_user(balance=7.0)
    db = _make_db()

    txn = await record_transaction(
        db, user, "charge", -3.0,
        "MT5 account setup: My Demo (12345@MetaQuotes-Demo)",
    )

    assert "MT5 account setup" in txn.description
    assert "12345@MetaQuotes-Demo" in txn.description
    assert txn.amount == -3.0
