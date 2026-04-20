"""add focused_paper_ids to chat_threads

Revision ID: c7d9e5f1a2b3
Revises: a1b2c3d4e5f6
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7d9e5f1a2b3"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # If table doesn't exist yet, skip — create_all will create it with the
    # column already defined in the model.
    tables = inspector.get_table_names(schema="BrieflyAI")
    if "chat_threads" not in tables:
        return

    # If column already exists (e.g. create_all ran first), skip.
    columns = [col["name"] for col in inspector.get_columns("chat_threads", schema="BrieflyAI")]
    if "focused_paper_ids" in columns:
        return

    op.add_column(
        "chat_threads",
        sa.Column("focused_paper_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="BrieflyAI",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names(schema="BrieflyAI")
    if "chat_threads" not in tables:
        return
    columns = [col["name"] for col in inspector.get_columns("chat_threads", schema="BrieflyAI")]
    if "focused_paper_ids" not in columns:
        return
    op.drop_column("chat_threads", "focused_paper_ids", schema="BrieflyAI")
