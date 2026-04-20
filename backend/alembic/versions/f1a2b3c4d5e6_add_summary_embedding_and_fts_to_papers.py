"""Add summary embedding and FTS to RP_abstract_data for multi-stage search.

Revision ID: f1a2b3c4d5e6
Revises: e2f4a1b9c8d7
Create Date: 2026-04-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector
from app.settings import get_settings


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e2f4a1b9c8d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add summary_embedding and summary_fts columns to RP_abstract_data."""
    # First, ensure pgvector extension exists
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    vector_dim = get_settings().embedding_vector_dim

    schema = "BrieflyAI"
    inspector = sa.inspect(op.get_bind())
    
    # Safely check if table exists
    tables = set(inspector.get_table_names(schema=schema))
    
    table_name = None
    for candidate in ("RP_abstract_data", "rp_abstract_data"):
        if candidate in tables:
            table_name = candidate
            break
    
    if table_name is None:
        # Table doesn't exist yet; skip this migration step
        return

    # Get current columns (with schema-aware inspection)
    try:
        columns = [col["name"] for col in inspector.get_columns(table_name, schema=schema)]
    except sa.exc.NoSuchTableError:
        # Table was removed between checks; exit safely
        return
    
    
    # Add summary_embedding (Vector type for 1024-dim embeddings)
    if "summary_embedding" not in columns:
        op.add_column(
            table_name,
            sa.Column("summary_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=vector_dim), nullable=True),
            schema=schema,
        )
    
    # Add summary_fts (Full-text search computed column)
    if "summary_fts" not in columns:
        op.add_column(
            table_name,
            sa.Column(
                "summary_fts",
                postgresql.TSVECTOR(),
                sa.Computed(
                    "to_tsvector('english', coalesce(summary,''))",
                    persisted=True,
                ),
                nullable=True,
            ),
            schema=schema,
        )
    
    # Add indices for vector and FTS search (idempotently)
    existing_indices = [
        idx["name"] for idx in inspector.get_indexes(table_name, schema=schema)
    ]
    
    if "idx_rp_summary_embedding" not in existing_indices:
        op.create_index(
            "idx_rp_summary_embedding",
            table_name,
            ["summary_embedding"],
            postgresql_using="ivfflat",
            schema=schema,
        )
    
    if "idx_rp_summary_fts_gin" not in existing_indices:
        op.create_index(
            "idx_rp_summary_fts_gin",
            table_name,
            ["summary_fts"],
            postgresql_using="gin",
            schema=schema,
        )


def downgrade() -> None:
    """Remove summary_embedding and summary_fts columns from RP_abstract_data."""
    schema = "BrieflyAI"
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names(schema=schema))

    table_name = None
    for candidate in ("RP_abstract_data", "rp_abstract_data"):
        if candidate in tables:
            table_name = candidate
            break
    if table_name is None:
        return

    # Drop indices
    existing_indices = [
        idx["name"] for idx in inspector.get_indexes(table_name, schema=schema)
    ]
    
    if "idx_rp_summary_embedding" in existing_indices:
        op.drop_index("idx_rp_summary_embedding", table_name=table_name, schema=schema)
    
    if "idx_rp_summary_fts_gin" in existing_indices:
        op.drop_index("idx_rp_summary_fts_gin", table_name=table_name, schema=schema)
    
    # Drop columns
    columns = [col["name"] for col in inspector.get_columns(table_name, schema=schema)]
    
    if "summary_fts" in columns:
        op.drop_column(table_name, "summary_fts", schema=schema)
    
    if "summary_embedding" in columns:
        op.drop_column(table_name, "summary_embedding", schema=schema)
