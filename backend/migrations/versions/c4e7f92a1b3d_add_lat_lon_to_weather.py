"""add lat lon to weather readings

Revision ID: c4e7f92a1b3d
Revises: 29d0831ebf0e
Create Date: 2026-06-26

"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e7f92a1b3d'
down_revision = '29d0831ebf0e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('weather_readings', sa.Column('lat', sa.Float(), nullable=True))
    op.add_column('weather_readings', sa.Column('lon', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('weather_readings', 'lon')
    op.drop_column('weather_readings', 'lat')
