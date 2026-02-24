import uuid
import uuid6
from datetime import datetime, timezone
import numpy as np
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String,
    Text,
    Integer,
    Boolean,
    Index,
    UniqueConstraint,
    ForeignKey,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase):
    __abstract__ = True


class RPAbstractData(Base):
    __tablename__ = "RP_abstract_data"

    __table_args__ = (

        UniqueConstraint("link", name="uq_research_papers_link"),

        Index("ix_research_papers_primary_category", "primary_category"),
        Index("ix_research_papers_published", "published"),
        Index("ix_research_papers_created_at", "created_at"),

        Index(
            "ix_research_papers_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_research_papers_summary_trgm",
            "summary",
            postgresql_using="gin",
            postgresql_ops={"summary": "gin_trgm_ops"},
        ),
                {"schema": "BrieflyAI"},
    )


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid6.uuid7
    )

    chunks = relationship(
        "Chunk",
        back_populates="rp_abstract",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    abstract_chunks = relationship(
        "paperAbstractChunk",
        back_populates="rp_abstract_chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    title: Mapped[str | None] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text)
    published: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    link: Mapped[str | None] = mapped_column(String(500))
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    primary_category: Mapped[str | None] = mapped_column(String(100))
    all_categories: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(200))
    journal_ref: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)

    attributes: Mapped[dict | None] = mapped_column(JSONB)

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),  
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),        
    )

class Chunk(Base):
    """
    Chunk Data

    Schema for RAG grounding.
    Designed to pair 1:1 with vector DB records.
    """

    __tablename__ = "chunk_data"
    __table_args__ = (
        UniqueConstraint(
            "rp_abstract_id",
            "chunk_index",
            name="uq_chunks_rp_chunk",
        ),
        Index(
            "idx_chunks_rp_order",
            "rp_abstract_id",
            "chunk_index",
        ),
        Index(
            "idx_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
        ),
        {"schema": "BrieflyAI"},
    )

    rp_abstract = relationship(
        "RPAbstractData",
        back_populates="chunks",
    )

    # ✅ Standard primary key
    id : Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    rp_abstract_id : Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "BrieflyAI.RP_abstract_data.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    doc_id : Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index : Mapped[int] = mapped_column(Integer, nullable=False)
    text : Mapped[str] = mapped_column(Text, nullable=False)
    llm_summary : Mapped[str | None] = mapped_column(Text, nullable=True)

    section : Mapped[str] = mapped_column(String(255), nullable=True)
    page_start : Mapped[int] = mapped_column(Integer, nullable=True)
    page_end : Mapped[int] = mapped_column(Integer, nullable=True)

    embedding : Mapped[np.ndarray] = mapped_column(Vector(1024), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        server_default=func.now(),  
    )



class paperAbstractChunk(Base):
    """
    Chunk Data

    Schema for RAG grounding.
    Designed to pair 1:1 with vector DB records.
    """

    __tablename__ = "paperAbstractChunk"
    __table_args__ = (
        UniqueConstraint(
            "rp_abstract_id",
            "chunk_index",
            name="uq_abstract_rp_chunk",
        ),
        Index(
            "idx_paper_abstract_chunks_rp_order",
            "rp_abstract_id",
            "chunk_index",
        ),
        Index(
            "idx_paper_abstract_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
        ),
        {"schema": "BrieflyAI"},
    )

    id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    rp_abstract_id : Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "BrieflyAI.RP_abstract_data.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    rp_abstract_chunk = relationship(
        "RPAbstractData",
        back_populates="abstract_chunks",
    )

    pdf_url : Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index : Mapped[int] = mapped_column(Integer, nullable=False)
    text : Mapped[str] = mapped_column(Text, nullable=False)
    llm_summary : Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding : Mapped[np.ndarray] = mapped_column(Vector(1024), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        server_default=func.now(),  
    )


class intentVector(Base):

    __tablename__ = "intent_vectors"
    __table_args__ = (
        {"schema": "BrieflyAI"},
    )

    # Examples:
    #   'global'
    #   'cs.LG'
    #   'cs.CL'
    #   'trending.cs.LG'
    name: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        nullable=False,
    )

    embedding = mapped_column(Vector(1024), nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),        
    )