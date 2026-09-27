"""Add Ship Captain approval workflow columns to users

Adds `status` (pending | approved | rejected), `reviewed_by`, and
`reviewed_at` to the existing `users` table. No new table, no new model -
this reuses the existing User/UserSession auth system and just adds the
approval gate Ship Captains need. control_station rows (and any existing
ship_captain rows created before this migration) default to "approved" so
current logins keep working unchanged.

Revision ID: c1d2e3f4a5b6
Revises: b3c4d5e6f7a8
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('status', sa.String(), nullable=False, server_default='approved'))
    op.add_column('users', sa.Column('reviewed_by', sa.String(), nullable=True))
    op.add_column('users', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'reviewed_at')
    op.drop_column('users', 'reviewed_by')
    op.drop_column('users', 'status')
