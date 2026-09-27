"""add ship profile fields (mmsi, captain, flag, dimensions, course/heading)

Revision ID: f1a2b3c4d5e6
Revises: e0907711dcdf
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e0907711dcdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ships', sa.Column('mmsi', sa.String(), nullable=True))
    op.add_column('ships', sa.Column('captain', sa.String(), nullable=True))
    op.add_column('ships', sa.Column('flag', sa.String(), nullable=True))
    op.add_column('ships', sa.Column('length_m', sa.Float(), nullable=True))
    op.add_column('ships', sa.Column('beam_m', sa.Float(), nullable=True))
    op.add_column('ships', sa.Column('course', sa.Float(), nullable=True))
    op.add_column('ships', sa.Column('heading', sa.Float(), nullable=True))
    op.add_column('ships', sa.Column('image_url', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ships', 'image_url')
    op.drop_column('ships', 'heading')
    op.drop_column('ships', 'course')
    op.drop_column('ships', 'beam_m')
    op.drop_column('ships', 'length_m')
    op.drop_column('ships', 'flag')
    op.drop_column('ships', 'captain')
    op.drop_column('ships', 'mmsi')
