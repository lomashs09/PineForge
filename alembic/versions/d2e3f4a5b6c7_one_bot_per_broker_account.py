"""bots: enforce one bot per broker_account at the schema level

Why:
  Two bots on the same broker account share positions, deal history, and
  trade attribution on the broker side. The application code already
  rejects creates that would violate this (see api/services/bot_service.py
  validate_bot_create), but a schema-level UNIQUE constraint is the
  unbypassable invariant — it stops bypass paths (direct DB writes,
  bulk imports, future endpoints that forget the check) from creating
  duplicates.

  Verified empty bots table at migration authoring time. If this
  migration fails on `alembic upgrade head` because duplicates exist,
  the operator must resolve them first (delete the redundant bot row,
  which cascades its bot_trades) before retrying.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-04-28 17:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pre-flight: surface duplicates with a helpful error rather than
    # letting the constraint creation fail with a generic message.
    op.execute(
        """
        DO $$
        DECLARE
            dup_count INT;
        BEGIN
            SELECT COUNT(*) INTO dup_count FROM (
                SELECT broker_account_id
                FROM bots
                GROUP BY broker_account_id
                HAVING COUNT(*) > 1
            ) sub;
            IF dup_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot apply one-bot-per-broker-account constraint: % broker_account(s) currently have multiple bots. Delete the extra bot row(s) first, then retry.',
                    dup_count;
            END IF;
        END $$;
        """
    )

    op.create_unique_constraint(
        "bots_broker_account_id_unique",
        "bots",
        ["broker_account_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "bots_broker_account_id_unique",
        "bots",
        type_="unique",
    )
