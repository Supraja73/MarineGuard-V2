"""Add active flag to users for System Administrator account management

Adds `active` (boolean, default true) to the existing `users` table so a
System Administrator (an existing Control Station operator) can disable
a Control Center account without deleting it. No new table - reuses the
same User model and login path added in the previous migration
(c1d2e3f4a5b6_add_user_approval_status).

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column('users', 'active')
