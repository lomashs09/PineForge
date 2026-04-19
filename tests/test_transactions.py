"""Comprehensive tests for the transaction audit trail system.

Covers:
- record_transaction() service (unit tests)
- Transaction model
- _handle_checkout_completed (Stripe webhook)
- _handle_checkout_expired (Stripe webhook)
- _mark_paypal_failed helper
- Usage billing transaction recording
- Description formatting for every transaction type
- Edge cases: None balance, rounding, duplicate webhooks, missing users
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.transaction import Transaction
from api.models.user import User
from api.services.transaction_service import record_transaction


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_user(balance=100.0, email="test@example.com", user_id=None):
    """Create a mock User object for testing."""
    user = MagicMock(spec=User)
    user.id = user_id or uuid.uuid4()
    user.email = email
    user.balance = balance
    user.is_admin = False
    user.full_name = "Test User"
    user.stripe_customer_id = "cus_test123"
    return user


def _make_db():
    """Create a mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


def _mock_db_query(db, return_value):
    """Mock db.execute(...).scalar_one_or_none() to return a value."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = return_value
    result_mock.scalars.return_value.all.return_value = (
        [return_value] if return_value else []
    )
    result_mock.scalar.return_value = 1 if return_value else 0
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ═══════════════════════════════════════════════════════════════════
# 1. record_transaction() unit tests
# ═══════════════════════════════════════════════════════════════════


class TestRecordTransaction:
    """Tests for the record_transaction service function."""

    @pytest.mark.asyncio
    async def test_deposit_positive_amount(self):
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
    async def test_charge_negative_amount(self):
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
    async def test_refund_positive_amount(self):
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
    async def test_manual_credit(self):
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
    async def test_rounds_amount_to_4dp(self):
        """Amounts should be rounded to 4 decimal places."""
        user = _make_user(balance=99.99999)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -0.001833333,
            "Usage billing (1 bot $0.0018)",
        )

        assert txn.amount == -0.0018
        assert txn.balance_after == 100.0

    @pytest.mark.asyncio
    async def test_no_reference_id(self):
        """Reference ID should be None when not provided."""
        user = _make_user(balance=50.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -3.0,
            "MT5 account setup",
        )

        assert txn.reference_id is None

    @pytest.mark.asyncio
    async def test_uuid_reference_converted_to_string(self):
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
    async def test_zero_balance(self):
        """Should handle zero balance correctly."""
        user = _make_user(balance=0.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 5.0,
            "First deposit",
        )

        assert txn.balance_after == 0.0

    @pytest.mark.asyncio
    async def test_none_balance_coerced_to_zero(self):
        """Should handle None balance (new user edge case)."""
        user = _make_user(balance=None)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 5.0,
            "First deposit",
        )

        assert txn.balance_after == 0.0

    @pytest.mark.asyncio
    async def test_negative_balance_recorded(self):
        """Should record negative balance if user somehow goes negative."""
        user = _make_user(balance=-2.5)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -0.50,
            "Usage billing",
        )

        assert txn.balance_after == -2.5

    @pytest.mark.asyncio
    async def test_very_large_deposit(self):
        """Should handle large amounts without overflow."""
        user = _make_user(balance=9999.9999)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 9999.9999,
            "Large deposit",
        )

        assert txn.amount == 9999.9999
        assert txn.balance_after == 9999.9999

    @pytest.mark.asyncio
    async def test_very_small_charge(self):
        """Should handle tiny billing charges (5-min interval)."""
        user = _make_user(balance=99.9982)
        db = _make_db()
        # $0.022/hr * (5/60)h = $0.001833...
        amount = round(-0.022 * (300 / 3600), 4)

        txn = await record_transaction(
            db, user, "charge", amount,
            "Usage billing (1 bot)",
        )

        assert txn.amount == -0.0018
        assert txn.type == "charge"

    @pytest.mark.asyncio
    async def test_deposit_pending_zero_amount(self):
        """Pending deposits should have zero amount (no balance change)."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_pending", 0,
            "Stripe checkout started: ₹500.00 → $5.9000",
            reference_id="cs_test_pending123",
        )

        assert txn.type == "deposit_pending"
        assert txn.amount == 0
        assert txn.balance_after == 10.0

    @pytest.mark.asyncio
    async def test_deposit_failed_zero_amount(self):
        """Failed deposits should have zero amount."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_failed", 0,
            "Stripe checkout expired: ₹500",
            reference_id="cs_test_expired456",
        )

        assert txn.type == "deposit_failed"
        assert txn.amount == 0
        assert txn.balance_after == 10.0


# ═══════════════════════════════════════════════════════════════════
# 2. Transaction model tests
# ═══════════════════════════════════════════════════════════════════


class TestTransactionModel:
    """Tests for the Transaction SQLAlchemy model."""

    def test_all_fields(self):
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

    def test_optional_reference_id(self):
        txn = Transaction(
            user_id=uuid.uuid4(),
            type="charge",
            amount=-3.0,
            balance_after=97.0,
            description="Account setup",
        )
        assert txn.reference_id is None

    def test_tablename(self):
        assert Transaction.__tablename__ == "transactions"

    def test_user_relationship_defined(self):
        """Transaction model should have a user relationship."""
        assert hasattr(Transaction, "user")

    def test_negative_amount_allowed(self):
        txn = Transaction(
            user_id=uuid.uuid4(),
            type="charge",
            amount=-0.152,
            balance_after=9.848,
            description="Bot start fee",
        )
        assert txn.amount == -0.152

    def test_all_transaction_types(self):
        """All expected transaction types should be storable."""
        types = ["deposit", "charge", "refund", "manual_credit",
                 "deposit_pending", "deposit_completed", "deposit_failed"]
        for tx_type in types:
            txn = Transaction(
                user_id=uuid.uuid4(),
                type=tx_type,
                amount=0,
                balance_after=0,
                description=f"Test {tx_type}",
            )
            assert txn.type == tx_type


# ═══════════════════════════════════════════════════════════════════
# 3. _handle_checkout_completed (Stripe webhook)
# ═══════════════════════════════════════════════════════════════════


class TestHandleCheckoutCompleted:
    """Tests for the Stripe checkout.session.completed webhook handler."""

    @pytest.mark.asyncio
    async def test_successful_inr_deposit(self):
        """Happy path: INR payment → balance credited, pending marked completed."""
        from api.routers.payments import _handle_checkout_completed

        user_id = uuid.uuid4()
        user = _make_user(balance=0.0, user_id=user_id)
        pending_txn = Transaction(
            user_id=user_id, type="deposit_pending", amount=0,
            balance_after=0, description="Stripe checkout started: ₹100.00 → $1.1800",
            reference_id="cs_test_success",
        )

        db = AsyncMock()
        # First call: find pending transaction
        # Second call: find user
        call_count = [0]
        def mock_execute(query):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # User lookup
                result.scalar_one_or_none.return_value = user
            elif call_count[0] == 2:
                # Pending transaction lookup
                result.scalar_one_or_none.return_value = pending_txn
            else:
                result.scalar_one_or_none.return_value = None
            return result
        db.execute = AsyncMock(side_effect=mock_execute)
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_success",
            "metadata": {
                "type": "add_funds",
                "user_id": str(user_id),
                "amount": "100",
                "currency": "INR",
                "usd_credit": "1.18",
            },
        }

        await _handle_checkout_completed(session, db)

        # Balance should be updated
        assert user.balance == 1.18
        # Pending should be marked completed
        assert pending_txn.type == "deposit_completed"
        assert "successful" in pending_txn.description
        # deposit transaction should be recorded via db.add
        assert db.add.called
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_usd_deposit(self):
        """USD payment should also work correctly."""
        from api.routers.payments import _handle_checkout_completed

        user_id = uuid.uuid4()
        user = _make_user(balance=5.0, user_id=user_id)

        db = AsyncMock()
        call_count = [0]
        def mock_execute(query):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = user
            else:
                result.scalar_one_or_none.return_value = None
            return result
        db.execute = AsyncMock(side_effect=mock_execute)
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_usd",
            "metadata": {
                "type": "add_funds",
                "user_id": str(user_id),
                "amount": "25",
                "currency": "USD",
                "usd_credit": "25",
            },
        }

        await _handle_checkout_completed(session, db)
        assert user.balance == 30.0

    @pytest.mark.asyncio
    async def test_ignores_non_add_funds_checkout(self):
        """Subscription checkouts should be silently ignored."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_sub",
            "metadata": {"type": "subscription"},
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_empty_metadata(self):
        """Sessions without metadata should be ignored."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        await _handle_checkout_completed({"id": "cs_test", "metadata": {}}, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_missing_metadata(self):
        """Sessions without metadata key should be ignored."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        await _handle_checkout_completed({"id": "cs_test"}, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_invalid_amount(self):
        """Non-numeric amount in metadata should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "not-a-number",
                "currency": "INR",
                "usd_credit": "also-bad",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_zero_credit(self):
        """Zero USD credit should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "0",
                "currency": "INR",
                "usd_credit": "0",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_negative_credit(self):
        """Negative USD credit should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "100",
                "currency": "INR",
                "usd_credit": "-5",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_excessive_credit(self):
        """Credits over $10,000 should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "1000000",
                "currency": "INR",
                "usd_credit": "15000",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_missing_user_id(self):
        """Missing user_id should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "amount": "100",
                "currency": "INR",
                "usd_credit": "1.18",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_malformed_user_id(self):
        """Non-UUID user_id should be rejected."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": "not-a-uuid",
                "amount": "100",
                "currency": "INR",
                "usd_credit": "1.18",
            },
        }

        await _handle_checkout_completed(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Should handle user not found in DB gracefully."""
        from api.routers.payments import _handle_checkout_completed

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "100",
                "currency": "INR",
                "usd_credit": "1.18",
            },
        }

        await _handle_checkout_completed(session, db)
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_record_still_credits(self):
        """Should still credit balance even if no pending transaction exists."""
        from api.routers.payments import _handle_checkout_completed

        user_id = uuid.uuid4()
        user = _make_user(balance=0.0, user_id=user_id)

        db = AsyncMock()
        call_count = [0]
        def mock_execute(query):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = user
            else:
                result.scalar_one_or_none.return_value = None  # no pending
            return result
        db.execute = AsyncMock(side_effect=mock_execute)
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_no_pending",
            "metadata": {
                "type": "add_funds",
                "user_id": str(user_id),
                "amount": "50",
                "currency": "USD",
                "usd_credit": "50",
            },
        }

        await _handle_checkout_completed(session, db)
        assert user.balance == 50.0
        db.commit.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# 4. _handle_checkout_expired (Stripe webhook)
# ═══════════════════════════════════════════════════════════════════


class TestHandleCheckoutExpired:
    """Tests for the Stripe checkout.session.expired webhook handler."""

    @pytest.mark.asyncio
    async def test_marks_pending_as_failed(self):
        """Should mark existing pending transaction as deposit_failed."""
        from api.routers.payments import _handle_checkout_expired

        pending = Transaction(
            user_id=uuid.uuid4(), type="deposit_pending", amount=0,
            balance_after=10.0, description="Stripe checkout started: ₹500.00 → $5.9000",
            reference_id="cs_test_expire",
        )

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = pending
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_expire",
            "metadata": {"type": "add_funds", "user_id": str(uuid.uuid4())},
        }

        await _handle_checkout_expired(session, db)

        assert pending.type == "deposit_failed"
        assert "expired/abandoned" in pending.description
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_failed_record_when_no_pending(self):
        """Should create a new deposit_failed if no pending record exists."""
        from api.routers.payments import _handle_checkout_expired

        user_id = uuid.uuid4()
        user = _make_user(balance=10.0, user_id=user_id)

        db = AsyncMock()
        call_count = [0]
        def mock_execute(query):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = None  # no pending
            else:
                result.scalar_one_or_none.return_value = user  # user lookup
            return result
        db.execute = AsyncMock(side_effect=mock_execute)
        db.add = MagicMock()
        db.commit = AsyncMock()

        session = {
            "id": "cs_test_no_pending_expire",
            "metadata": {
                "type": "add_funds",
                "user_id": str(user_id),
                "amount": "500",
                "currency": "INR",
            },
        }

        await _handle_checkout_expired(session, db)
        # Should have created a deposit_failed transaction
        assert db.add.called

    @pytest.mark.asyncio
    async def test_ignores_non_add_funds(self):
        """Should ignore subscription checkout expirations."""
        from api.routers.payments import _handle_checkout_expired

        db = AsyncMock()
        db.commit = AsyncMock()
        db.add = MagicMock()

        session = {
            "id": "cs_test_sub_expire",
            "metadata": {"type": "subscription"},
        }

        await _handle_checkout_expired(session, db)
        db.commit.assert_not_called()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_empty_metadata(self):
        """Should ignore sessions without add_funds type."""
        from api.routers.payments import _handle_checkout_expired

        db = AsyncMock()
        db.commit = AsyncMock()
        db.add = MagicMock()

        await _handle_checkout_expired({"id": "cs_test", "metadata": {}}, db)
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_no_user_id(self):
        """Should handle missing user_id gracefully when no pending record."""
        from api.routers.payments import _handle_checkout_expired

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()

        session = {
            "id": "cs_test",
            "metadata": {"type": "add_funds"},
        }

        await _handle_checkout_expired(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_invalid_user_id(self):
        """Should handle invalid user_id gracefully when no pending record."""
        from api.routers.payments import _handle_checkout_expired

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()

        session = {
            "id": "cs_test",
            "metadata": {"type": "add_funds", "user_id": "bad-uuid"},
        }

        await _handle_checkout_expired(session, db)
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_user_not_found(self):
        """Should handle user not found when no pending record."""
        from api.routers.payments import _handle_checkout_expired

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None  # no pending AND no user
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()

        session = {
            "id": "cs_test",
            "metadata": {
                "type": "add_funds",
                "user_id": str(uuid.uuid4()),
                "amount": "100",
                "currency": "INR",
            },
        }

        await _handle_checkout_expired(session, db)
        # No user found → nothing recorded
        db.add.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 5. _mark_paypal_failed
# ═══════════════════════════════════════════════════════════════════


class TestMarkPaypalFailed:
    """Tests for the _mark_paypal_failed helper."""

    @pytest.mark.asyncio
    async def test_updates_existing_pending(self):
        """Should mark existing pending transaction as failed with reason."""
        from api.routers.payments import _mark_paypal_failed

        pending = Transaction(
            user_id=uuid.uuid4(), type="deposit_pending", amount=0,
            balance_after=10.0, description="PayPal checkout started: $10.00",
            reference_id="PP-ORDER-123",
        )

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = pending
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()

        user = _make_user(balance=10.0)
        await _mark_paypal_failed(db, user, "PP-ORDER-123", "Capture HTTP 400")

        assert pending.type == "deposit_failed"
        assert "Capture HTTP 400" in pending.description
        db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_new_failed_when_no_pending(self):
        """Should create new deposit_failed if no pending exists."""
        from api.routers.payments import _mark_paypal_failed

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()

        user = _make_user(balance=10.0)
        await _mark_paypal_failed(db, user, "PP-ORDER-NOPENDING", "Amount mismatch")

        # Should create via record_transaction → db.add
        assert db.add.called
        txn = db.add.call_args[0][0]
        assert txn.type == "deposit_failed"
        assert "Amount mismatch" in txn.description

    @pytest.mark.asyncio
    async def test_reason_appended_to_description(self):
        """Reason should be appended after ' — ' to the pending description."""
        from api.routers.payments import _mark_paypal_failed

        pending = Transaction(
            user_id=uuid.uuid4(), type="deposit_pending", amount=0,
            balance_after=5.0, description="PayPal checkout started: $25.00",
            reference_id="PP-FAIL",
        )

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = pending
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()

        user = _make_user()
        await _mark_paypal_failed(db, user, "PP-FAIL", "Status: VOIDED")

        assert pending.description == "PayPal checkout started: $25.00 — Status: VOIDED"


# ═══════════════════════════════════════════════════════════════════
# 6. Usage billing transaction recording
# ═══════════════════════════════════════════════════════════════════


class TestUsageBillingTransactions:
    """Tests for usage billing transaction description formatting."""

    @pytest.mark.asyncio
    async def test_single_bot_single_account(self):
        """Single bot + single account billing tick."""
        user = _make_user(balance=99.998)
        db = _make_db()

        bot_cost = 0.022 * (300 / 3600)
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
    async def test_multiple_bots_multiple_accounts(self):
        """Multiple bots + accounts should pluralize."""
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

    @pytest.mark.asyncio
    async def test_bots_only_no_accounts(self):
        """Billing with only bots, no account charges."""
        user = _make_user(balance=50.0)
        db = _make_db()

        bot_cost = 2 * 0.022 * (300 / 3600)
        parts = [f"2 bots ${bot_cost:.4f}"]
        desc = f"Usage billing ({', '.join(parts)})"

        txn = await record_transaction(db, user, "charge", -bot_cost, desc)

        assert "2 bots" in txn.description
        assert "acct" not in txn.description

    @pytest.mark.asyncio
    async def test_accounts_only_no_bots(self):
        """Billing with only account hosting, no bots."""
        user = _make_user(balance=50.0)
        db = _make_db()

        account_cost = 3 * 0.002 * (300 / 3600)
        parts = [f"3 accts ${account_cost:.4f}"]
        desc = f"Usage billing ({', '.join(parts)})"

        txn = await record_transaction(db, user, "charge", -account_cost, desc)

        assert "3 accts" in txn.description
        assert "bot" not in txn.description

    @pytest.mark.asyncio
    async def test_billing_amount_matches_rates(self):
        """Billing amount should match expected rates exactly."""
        user = _make_user(balance=100.0)
        db = _make_db()

        interval_hours = 300 / 3600  # 5 minutes
        bot_cost = 1 * 0.022 * interval_hours  # $0.001833...
        account_cost = 1 * 0.002 * interval_hours  # $0.000166...
        total = round(-(bot_cost + account_cost), 4)

        txn = await record_transaction(db, user, "charge", total, "Usage billing")

        assert txn.amount == round(total, 4)


# ═══════════════════════════════════════════════════════════════════
# 7. Deposit description formatting
# ═══════════════════════════════════════════════════════════════════


class TestDepositDescriptions:
    """Tests for deposit transaction descriptions across all providers."""

    @pytest.mark.asyncio
    async def test_stripe_inr(self):
        user = _make_user(balance=11.18)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 1.18,
            "Stripe ₹100.00 → $1.1800",
            reference_id="cs_test_xyz",
        )

        assert "₹100.00" in txn.description
        assert "$1.1800" in txn.description
        assert txn.amount == 1.18

    @pytest.mark.asyncio
    async def test_stripe_usd(self):
        user = _make_user(balance=55.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 50.0,
            "Stripe $50.00 → $50.0000",
            reference_id="cs_test_usd",
        )

        assert "$50.00" in txn.description

    @pytest.mark.asyncio
    async def test_paypal(self):
        user = _make_user(balance=60.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 50.0,
            "PayPal $50.00",
            reference_id="PAYPAL-ORDER-123",
        )

        assert "PayPal $50.00" in txn.description
        assert txn.amount == 50.0

    @pytest.mark.asyncio
    async def test_razorpay_inr(self):
        user = _make_user(balance=3.54)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 2.36,
            "Razorpay ₹200.00 → $2.3600",
            reference_id="pay_rzp_test",
        )

        assert "Razorpay" in txn.description
        assert "₹200.00" in txn.description

    @pytest.mark.asyncio
    async def test_stripe_pending_inr(self):
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_pending", 0,
            "Stripe checkout started: ₹500.00 → $5.9000",
            reference_id="cs_pending",
        )

        assert "checkout started" in txn.description
        assert "₹500.00" in txn.description

    @pytest.mark.asyncio
    async def test_paypal_pending(self):
        user = _make_user(balance=25.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_pending", 0,
            "PayPal checkout started: $10.00",
            reference_id="PAYPAL-456",
        )

        assert "PayPal checkout started" in txn.description

    @pytest.mark.asyncio
    async def test_stripe_expired(self):
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_failed", 0,
            "Stripe checkout expired: ₹500",
            reference_id="cs_expired",
        )

        assert "expired" in txn.description

    @pytest.mark.asyncio
    async def test_paypal_capture_failed(self):
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit_failed", 0,
            "PayPal capture failed: Capture HTTP 400",
            reference_id="PP-FAIL",
        )

        assert "capture failed" in txn.description


# ═══════════════════════════════════════════════════════════════════
# 8. Charge description formatting
# ═══════════════════════════════════════════════════════════════════


class TestChargeDescriptions:
    """Tests for charge/refund transaction descriptions."""

    @pytest.mark.asyncio
    async def test_bot_deployment(self):
        user = _make_user(balance=9.848)
        db = _make_db()
        bot_id = uuid.uuid4()

        txn = await record_transaction(
            db, user, "charge", -0.152,
            "Bot start: XAUUSD Scalper (XAUUSD 5m) — deploy + 1hr prepaid",
            reference_id=str(bot_id),
        )

        assert "Bot start" in txn.description
        assert "XAUUSD" in txn.description
        assert "deploy + 1hr prepaid" in txn.description
        assert txn.amount == -0.152

    @pytest.mark.asyncio
    async def test_bot_refund_with_error(self):
        user = _make_user(balance=10.152)
        db = _make_db()
        bot_id = uuid.uuid4()

        txn = await record_transaction(
            db, user, "refund", 0.152,
            "Bot start failed (refund): MyBot — MetaAPI connection timeout",
            reference_id=str(bot_id),
        )

        assert "refund" in txn.description
        assert "MyBot" in txn.description
        assert "MetaAPI connection timeout" in txn.description
        assert txn.amount == 0.152
        assert txn.type == "refund"

    @pytest.mark.asyncio
    async def test_account_setup(self):
        user = _make_user(balance=7.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -3.0,
            "MT5 account setup: My Demo (12345@MetaQuotes-Demo)",
        )

        assert "MT5 account setup" in txn.description
        assert "12345@MetaQuotes-Demo" in txn.description
        assert txn.amount == -3.0

    @pytest.mark.asyncio
    async def test_account_setup_no_reference(self):
        """Account setup charges have no reference_id."""
        user = _make_user(balance=7.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -3.0,
            "MT5 account setup: Test (999@Server)",
            reference_id=None,
        )

        assert txn.reference_id is None


# ═══════════════════════════════════════════════════════════════════
# 9. Pending → completed/failed lifecycle flows
# ═══════════════════════════════════════════════════════════════════


class TestLifecycleFlows:
    """Tests for full pending → completed/failed transaction flows."""

    @pytest.mark.asyncio
    async def test_stripe_pending_to_completed(self):
        """Full flow: pending → deposit_completed + deposit."""
        user = _make_user(balance=11.18)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "Stripe checkout started: ₹100.00 → $1.1800",
            reference_id="cs_flow",
        )
        assert pending.type == "deposit_pending"

        # Simulate webhook
        pending.type = "deposit_completed"
        pending.description = "Stripe payment successful: ₹100.00"
        assert pending.type == "deposit_completed"
        assert "successful" in pending.description

        deposit = await record_transaction(
            db, user, "deposit", 1.18,
            "Stripe ₹100.00 → $1.1800",
            reference_id="cs_flow",
        )
        assert deposit.type == "deposit"
        assert deposit.amount == 1.18

    @pytest.mark.asyncio
    async def test_stripe_pending_to_failed(self):
        """Full flow: pending → deposit_failed on expiry."""
        user = _make_user(balance=10.0)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "Stripe checkout started: ₹500.00 → $5.9000",
            reference_id="cs_expire_flow",
        )

        pending.type = "deposit_failed"
        pending.description += " — expired/abandoned"

        assert pending.type == "deposit_failed"
        assert "expired/abandoned" in pending.description
        # Balance should NOT have changed
        assert user.balance == 10.0

    @pytest.mark.asyncio
    async def test_paypal_pending_to_completed(self):
        """Full flow: PayPal pending → deposit_completed + deposit."""
        user = _make_user(balance=60.0)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "PayPal checkout started: $50.00",
            reference_id="PP-FLOW",
        )

        pending.type = "deposit_completed"
        pending.description = "PayPal payment successful: $50.00"

        deposit = await record_transaction(
            db, user, "deposit", 50.0,
            "PayPal $50.00",
            reference_id="PP-FLOW",
        )

        assert pending.type == "deposit_completed"
        assert deposit.type == "deposit"
        assert deposit.amount == 50.0

    @pytest.mark.asyncio
    async def test_paypal_pending_to_failed_http_error(self):
        """Full flow: PayPal pending → failed on HTTP error."""
        user = _make_user(balance=10.0)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "PayPal checkout started: $25.00",
            reference_id="PP-HTTPFAIL",
        )

        pending.type = "deposit_failed"
        pending.description += " — Capture HTTP 500"

        assert pending.type == "deposit_failed"
        assert "Capture HTTP 500" in pending.description

    @pytest.mark.asyncio
    async def test_paypal_pending_to_failed_status_error(self):
        """Full flow: PayPal pending → failed on non-COMPLETED status."""
        user = _make_user(balance=10.0)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "PayPal checkout started: $15.00",
            reference_id="PP-VOIDED",
        )

        pending.type = "deposit_failed"
        pending.description += " — Status: VOIDED"

        assert "Status: VOIDED" in pending.description

    @pytest.mark.asyncio
    async def test_paypal_pending_to_failed_amount_mismatch(self):
        """Full flow: PayPal pending → failed on amount mismatch."""
        user = _make_user(balance=10.0)
        db = _make_db()

        pending = await record_transaction(
            db, user, "deposit_pending", 0,
            "PayPal checkout started: $30.00",
            reference_id="PP-MISMATCH",
        )

        pending.type = "deposit_failed"
        pending.description += " — Amount mismatch"

        assert "Amount mismatch" in pending.description

    @pytest.mark.asyncio
    async def test_bot_charge_then_refund(self):
        """Full flow: bot start charge → failure → refund."""
        user = _make_user(balance=10.0)
        db = _make_db()
        bot_id = uuid.uuid4()

        # Charge
        user.balance = round(user.balance - 0.152, 4)
        charge = await record_transaction(
            db, user, "charge", -0.152,
            f"Bot start: TestBot (EURUSD 1h) — deploy + 1hr prepaid",
            reference_id=str(bot_id),
        )
        assert charge.amount == -0.152
        assert user.balance == 9.848

        # Refund
        user.balance = round(user.balance + 0.152, 4)
        refund = await record_transaction(
            db, user, "refund", 0.152,
            f"Bot start failed (refund): TestBot — connection refused",
            reference_id=str(bot_id),
        )
        assert refund.amount == 0.152
        assert user.balance == 10.0

    @pytest.mark.asyncio
    async def test_multiple_transactions_same_reference(self):
        """Multiple transactions can share the same reference_id."""
        user = _make_user(balance=11.18)
        db = _make_db()
        ref = "cs_shared_ref"

        pending = await record_transaction(
            db, user, "deposit_pending", 0, "Pending", reference_id=ref,
        )
        deposit = await record_transaction(
            db, user, "deposit", 1.18, "Completed", reference_id=ref,
        )

        assert pending.reference_id == ref
        assert deposit.reference_id == ref
        assert pending.type != deposit.type


# ═══════════════════════════════════════════════════════════════════
# 10. Edge cases and invariants
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Tests for edge cases and data integrity."""

    @pytest.mark.asyncio
    async def test_long_description_truncation(self):
        """Descriptions up to 500 chars should work."""
        user = _make_user(balance=10.0)
        db = _make_db()

        long_desc = "A" * 500
        txn = await record_transaction(db, user, "charge", -1.0, long_desc)
        assert len(txn.description) == 500

    @pytest.mark.asyncio
    async def test_unicode_in_description(self):
        """Unicode characters (₹, →) should be preserved."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 1.0,
            "Stripe ₹100 → $1.18 — résumé café",
        )
        assert "₹" in txn.description
        assert "→" in txn.description
        assert "résumé" in txn.description

    @pytest.mark.asyncio
    async def test_zero_amount_transaction(self):
        """Zero-amount transactions (pending/failed) are valid."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(db, user, "deposit_pending", 0, "Pending")
        assert txn.amount == 0

    @pytest.mark.asyncio
    async def test_precision_maintained_over_many_transactions(self):
        """Floating-point drift should be controlled by 4dp rounding."""
        user = _make_user(balance=100.0)
        db = _make_db()

        charge = round(-0.022 * (300 / 3600), 4)  # -0.0018
        for _ in range(100):
            user.balance = round(user.balance + charge, 4)
            txn = await record_transaction(db, user, "charge", charge, "Billing")

        # After 100 ticks of $0.0018 each = $0.18 total
        expected = round(100.0 + (charge * 100), 4)
        assert user.balance == expected
        assert txn.balance_after == expected

    @pytest.mark.asyncio
    async def test_transaction_added_to_db_session(self):
        """Every call to record_transaction must add to the DB session."""
        user = _make_user(balance=10.0)
        db = _make_db()

        await record_transaction(db, user, "deposit", 5.0, "Test 1")
        await record_transaction(db, user, "charge", -1.0, "Test 2")
        await record_transaction(db, user, "deposit_pending", 0, "Test 3")

        assert db.add.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_reference_id_is_none(self):
        """Passing None reference_id should store None, not 'None'."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "charge", -3.0, "Test", reference_id=None,
        )
        assert txn.reference_id is None

    @pytest.mark.asyncio
    async def test_string_reference_id_preserved(self):
        """String reference IDs should be preserved exactly."""
        user = _make_user(balance=10.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 5.0, "Test",
            reference_id="cs_live_abc123XYZ",
        )
        assert txn.reference_id == "cs_live_abc123XYZ"

    @pytest.mark.asyncio
    async def test_all_fields_populated(self):
        """Every transaction should have all required fields set."""
        user = _make_user(balance=42.0)
        db = _make_db()

        txn = await record_transaction(
            db, user, "deposit", 10.0, "Complete test",
            reference_id="ref_complete",
        )

        assert txn.user_id is not None
        assert txn.type is not None
        assert txn.amount is not None
        assert txn.balance_after is not None
        assert txn.description is not None
        assert txn.reference_id is not None
