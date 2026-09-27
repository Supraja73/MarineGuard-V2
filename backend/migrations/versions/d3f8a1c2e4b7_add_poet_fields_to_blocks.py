"""add poet fields to blocks (proposer_id, poet_wait_ms, poet_draws)

Revision ID: d3f8a1c2e4b7
Revises: c9b497ab1b2d
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f8a1c2e4b7'
down_revision: Union[str, Sequence[str], None] = 'c9b497ab1b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('blocks', sa.Column('proposer_id', sa.String(), nullable=True))
    op.add_column('blocks', sa.Column('poet_wait_ms', sa.Float(), nullable=True))
    op.add_column('blocks', sa.Column('poet_draws', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('blocks', 'poet_draws')
    op.drop_column('blocks', 'poet_wait_ms')
    op.drop_column('blocks', 'proposer_id')
