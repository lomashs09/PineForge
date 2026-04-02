"""add email verification columns

Revision ID: a2b3c4d5e6f7
Revises: 1ce30ff1e59b
Create Date: 2026-04-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "1ce30ff1e59b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_token", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "is_email_verified")
