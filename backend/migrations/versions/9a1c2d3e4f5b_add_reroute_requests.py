"""Add reroute_requests table (cyclone safe-route / safe-port approval workflow)

Revision ID: 9a1c2d3e4f5b
Revises: d3f8a1c2e4b7
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c2d3e4f5b'
down_revision: Union[str, Sequence[str], None] = 'd3f8a1c2e4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'reroute_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('voyage_id', sa.Integer(), nullable=False),
        sa.Column('ship_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('cyclone_name', sa.String(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('proposed_waypoints_json', sa.Text(), nullable=True),
        sa.Column('proposed_distance_km', sa.Float(), nullable=True),
        sa.Column('proposed_port', sa.String(), nullable=True),
        sa.Column('proposed_port_lat', sa.Float(), nullable=True),
        sa.Column('proposed_port_lon', sa.Float(), nullable=True),
        sa.Column('original_dest_port', sa.String(), nullable=True),
        sa.Column('original_dest_lat', sa.Float(), nullable=True),
        sa.Column('original_dest_lon', sa.Float(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('decided_by', sa.String(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['voyage_id'], ['voyages.voyage_id']),
        sa.ForeignKeyConstraint(['ship_id'], ['ships.ship_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_reroute_requests_voyage_id'), 'reroute_requests', ['voyage_id'], unique=False)
    op.create_index(op.f('ix_reroute_requests_ship_id'), 'reroute_requests', ['ship_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_reroute_requests_ship_id'), table_name='reroute_requests')
    op.drop_index(op.f('ix_reroute_requests_voyage_id'), table_name='reroute_requests')
    op.drop_table('reroute_requests')
