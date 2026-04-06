"""add user balance

Revision ID: f1a2b3c4d5e6
Revises: e86f301bfdfd
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e86f301bfdfd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('balance', sa.Float(), server_default=sa.text('0.0'), nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'balance')
