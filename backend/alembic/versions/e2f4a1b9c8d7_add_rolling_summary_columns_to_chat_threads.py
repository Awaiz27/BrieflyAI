"""add rolling summary columns to chat_threads

Revision ID: e2f4a1b9c8d7
Revises: c7d9e5f1a2b3
Create Date: 2026-04-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f4a1b9c8d7"
down_revision: Union[str, Sequence[str], None] = "c7d9e5f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names(schema="BrieflyAI")
    if "chat_threads" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("chat_threads", schema="BrieflyAI")}

    if "rolling_summary" not in columns:
        op.add_column(
            "chat_threads",
            sa.Column("rolling_summary", sa.Text(), nullable=True),
            schema="BrieflyAI",
        )
    if "rolling_summary_msg_count" not in columns:
        op.add_column(
            "chat_threads",
            sa.Column("rolling_summary_msg_count", sa.Integer(), nullable=False, server_default="0"),
            schema="BrieflyAI",
        )
    if "rolling_summary_updated_at" not in columns:
        op.add_column(
            "chat_threads",
            sa.Column("rolling_summary_updated_at", sa.DateTime(timezone=True), nullable=True),
            schema="BrieflyAI",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names(schema="BrieflyAI")
    if "chat_threads" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("chat_threads", schema="BrieflyAI")}

    if "rolling_summary_updated_at" in columns:
        op.drop_column("chat_threads", "rolling_summary_updated_at", schema="BrieflyAI")
    if "rolling_summary_msg_count" in columns:
        op.drop_column("chat_threads", "rolling_summary_msg_count", schema="BrieflyAI")
    if "rolling_summary" in columns:
        op.drop_column("chat_threads", "rolling_summary", schema="BrieflyAI")
