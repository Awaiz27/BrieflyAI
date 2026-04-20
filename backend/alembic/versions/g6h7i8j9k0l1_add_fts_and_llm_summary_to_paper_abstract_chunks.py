"""Add FTS column to paperAbstractChunk table.

Revision ID: g6h7i8j9k0l1
Revises: f1a2b3c4d5e6
Create Date: 2026-04-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


# revision identifiers, used by Alembic.
revision: str = 'g6h7i8j9k0l1'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add fts column (computed, generated from text)
    op.add_column(
        'paperAbstractChunk',
        sa.Column(
            'fts',
            TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(text,''))", persisted=True),
            nullable=False
        ),
        schema='BrieflyAI'
    )
    
    # Add GIN index on fts column for full-text search
    op.create_index(
        'idx_pac_fts_gin',
        'paperAbstractChunk',
        ['fts'],
        schema='BrieflyAI',
        postgresql_using='gin'
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the GIN index
    op.drop_index('idx_pac_fts_gin', table_name='paperAbstractChunk', schema='BrieflyAI')
    
    # Drop fts column
    op.drop_column('paperAbstractChunk', 'fts', schema='BrieflyAI')
