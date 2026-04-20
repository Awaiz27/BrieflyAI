"""add auth columns to app_users

Revision ID: a1b2c3d4e5f6
Revises: d885caa91e0e
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd885caa91e0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_users',
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='BrieflyAI',
    )
    op.create_index(
        'ix_app_users_email',
        'app_users',
        ['email'],
        unique=True,
        schema='BrieflyAI',
    )


def downgrade() -> None:
    op.drop_index('ix_app_users_email', table_name='app_users', schema='BrieflyAI')
    op.drop_table('app_users', schema='BrieflyAI')
