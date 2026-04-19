"""add bot magic_number for trade isolation

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-19

"""
import random

from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column with default 0
    op.add_column('bots', sa.Column('magic_number', sa.Integer(), server_default=sa.text('0'), nullable=False))

    # Backfill existing bots with unique magic numbers
    conn = op.get_bind()
    bots = conn.execute(sa.text("SELECT id FROM bots")).fetchall()
    for bot in bots:
        magic = random.randint(100_000, 2_147_483_647)
        conn.execute(
            sa.text("UPDATE bots SET magic_number = :magic WHERE id = :id"),
            {"magic": magic, "id": bot[0]},
        )


def downgrade() -> None:
    op.drop_column('bots', 'magic_number')
