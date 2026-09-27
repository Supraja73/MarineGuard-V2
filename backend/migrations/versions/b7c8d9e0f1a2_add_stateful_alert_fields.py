"""add stateful alert fields (resolved, resolved_at, last_seen_at, occurrence_count)

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('resolved', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('alerts', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alerts', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alerts', sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='1'))
    # Index used by the new "is there already an active alert of this type
    # for this ship" lookup that every detection cycle now runs.
    op.create_index('ix_alerts_active_lookup', 'alerts', ['ship_id', 'alert_type', 'resolved'])


def downgrade() -> None:
    op.drop_index('ix_alerts_active_lookup', table_name='alerts')
    op.drop_column('alerts', 'occurrence_count')
    op.drop_column('alerts', 'last_seen_at')
    op.drop_column('alerts', 'resolved_at')
    op.drop_column('alerts', 'resolved')
