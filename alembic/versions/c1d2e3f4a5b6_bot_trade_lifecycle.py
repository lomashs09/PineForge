"""bot_trades: lifecycle_state column + idempotent unique index

Why:
  bot_trades had no idempotency key, so reconciliation racing with normal
  trade recording could in principle insert duplicate rows for the same
  broker order. Adds a partial unique index on (bot_id, order_id) skipping
  the synthetic close-all and dry-run rows whose order_ids aren't unique
  by design.

  Adds lifecycle_state so the trade row's history is queryable beyond a
  bare closed_at timestamp:
    - open                 — bot opened, broker confirmed, still on broker
    - closing              — bot has issued a close request, awaiting confirm
    - closed               — closed by the bot, exit_price/pnl recorded
    - reconciled_external  — closed on broker without our knowing (manual
                             close on MT5, SL/TP fill we missed, etc.).
                             Position reconciliation backfills.
    - error                — terminal failure state for cleanup queries

  Existing rows are backfilled: rows with closed_at IS NULL keep state
  'open' (or 'reconciled_external' for past orphans we already closed
  with NULL pnl), rows with closed_at AND pnl get 'closed'.

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-04-27 15:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_trades",
        sa.Column(
            "lifecycle_state",
            sa.String(length=24),
            nullable=False,
            server_default="open",
        ),
    )

    op.execute(
        """
        UPDATE bot_trades
        SET lifecycle_state = CASE
            WHEN closed_at IS NULL                                  THEN 'open'
            WHEN closed_at IS NOT NULL AND pnl IS NOT NULL          THEN 'closed'
            WHEN closed_at IS NOT NULL AND pnl IS NULL              THEN 'reconciled_external'
            ELSE 'open'
        END
        """
    )

    op.create_check_constraint(
        "bot_trades_lifecycle_state_check",
        "bot_trades",
        "lifecycle_state IN ('open', 'closing', 'closed', 'reconciled_external', 'error')",
    )

    # Partial unique index — broker order_ids are unique per bot, but the
    # synthetic 'close-all' and 'dry-run' sentinel order_ids are reused.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_bot_trades_bot_order_unique
        ON bot_trades (bot_id, order_id)
        WHERE order_id IS NOT NULL
          AND order_id NOT LIKE 'close-all%'
          AND order_id NOT LIKE 'dry-run%'
        """
    )

    op.create_index(
        "ix_bot_trades_lifecycle_state",
        "bot_trades",
        ["lifecycle_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_trades_lifecycle_state", table_name="bot_trades")
    op.drop_index("ix_bot_trades_bot_order_unique", table_name="bot_trades")
    op.drop_constraint("bot_trades_lifecycle_state_check", "bot_trades", type_="check")
    op.drop_column("bot_trades", "lifecycle_state")
