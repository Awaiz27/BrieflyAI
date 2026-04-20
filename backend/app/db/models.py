"""SQLAlchemy ORM models — single source of truth for the database schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import numpy as np
import uuid6
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.settings import get_settings

_SCHEMA = get_settings().postgres_schema
_VECTOR_DIM = get_settings().embedding_vector_dim

# ── Base ────────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    __abstract__ = True


# ── Research Papers ─────────────────────────────────────────────────────────


class RPAbstractData(Base):
    __tablename__ = "RP_abstract_data"
    __table_args__ = (
        UniqueConstraint("link", name="uq_research_papers_link"),
        Index("ix_rp_primary_category", "primary_category"),
        Index("ix_rp_published", "published"),
        Index("ix_rp_created_at", "created_at"),
        Index(
            "ix_rp_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_rp_summary_trgm",
            "summary",
            postgresql_using="gin",
            postgresql_ops={"summary": "gin_trgm_ops"},
        ),
        Index("idx_rp_summary_embedding", "summary_embedding", postgresql_using="ivfflat"),
        Index("idx_rp_summary_fts_gin", "summary_fts", postgresql_using="gin"),
        {"schema": _SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    summary_embedding: Mapped[Optional[np.ndarray]] = mapped_column(Vector(_VECTOR_DIM), nullable=True)
    authors: Mapped[Optional[str]] = mapped_column(Text)
    summary_fts: Mapped[Optional[str]] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(summary,''))", persisted=True), nullable=True
    )
    published: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    updated: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    link: Mapped[Optional[str]] = mapped_column(String(500))
    pdf_url: Mapped[Optional[str]] = mapped_column(String(500))
    primary_category: Mapped[Optional[str]] = mapped_column(String(100))
    all_categories: Mapped[Optional[str]] = mapped_column(Text)
    doi: Mapped[Optional[str]] = mapped_column(String(200))
    journal_ref: Mapped[Optional[str]] = mapped_column(Text)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    attributes: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="paper", cascade="all, delete-orphan", passive_deletes=True)
    abstract_chunks: Mapped[list["PaperAbstractChunk"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", passive_deletes=True
    )


# ── Chunks ──────────────────────────────────────────────────────────────────


class Chunk(Base):
    __tablename__ = "chunk_data"
    __table_args__ = (
        UniqueConstraint("rp_abstract_id", "chunk_index", name="uq_chunks_rp_chunk"),
        Index("idx_chunks_rp_order", "rp_abstract_id", "chunk_index"),
        Index("idx_chunks_embedding", "embedding", postgresql_using="ivfflat"),
        Index("idx_chunks_fts_gin", "fts", postgresql_using="gin"),
        {"schema": _SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rp_abstract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_SCHEMA}.RP_abstract_data.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    llm_summary: Mapped[Optional[str]] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    page_start: Mapped[Optional[int]] = mapped_column(Integer)
    page_end: Mapped[Optional[int]] = mapped_column(Integer)
    embedding: Mapped[np.ndarray] = mapped_column(Vector(_VECTOR_DIM), nullable=False)
    fts: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(text,''))", persisted=True), nullable=False
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    paper: Mapped["RPAbstractData"] = relationship(back_populates="chunks")


class PaperAbstractChunk(Base):
    __tablename__ = "paperAbstractChunk"
    __table_args__ = (
        UniqueConstraint("rp_abstract_id", "chunk_index", name="uq_abstract_rp_chunk"),
        Index("idx_pac_rp_order", "rp_abstract_id", "chunk_index"),
        Index("idx_pac_embedding", "embedding", postgresql_using="ivfflat"),
        Index("idx_pac_fts_gin", "fts", postgresql_using="gin"),
        {"schema": _SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rp_abstract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_SCHEMA}.RP_abstract_data.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pdf_url: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    llm_summary: Mapped[Optional[str]] = mapped_column(Text)
    embedding: Mapped[np.ndarray] = mapped_column(Vector(_VECTOR_DIM), nullable=False)
    fts: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(text,''))", persisted=True), nullable=False
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    paper: Mapped["RPAbstractData"] = relationship(back_populates="abstract_chunks")


# ── Intent Vectors ──────────────────────────────────────────────────────────


class IntentVector(Base):
    __tablename__ = "intent_vectors"
    __table_args__ = ({"schema": _SCHEMA},)

    name: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    embedding = mapped_column(Vector(_VECTOR_DIM), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ── Users & Auth ────────────────────────────────────────────────────────────


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = (
        Index("ix_app_users_email", "email", unique=True),
        {"schema": _SCHEMA},
    )

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── Chat ────────────────────────────────────────────────────────────────────


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        Index("idx_chat_threads_user", "user_id"),
        {"schema": _SCHEMA},
    )

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.app_users.user_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    focused_paper_ids: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    rolling_summary: Mapped[Optional[str]] = mapped_column(Text)
    rolling_summary_msg_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rolling_summary_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system','tool')", name="ck_chat_messages_role"),
        Index("idx_chat_messages_chat", "chat_id", "msg_id"),
        {"schema": _SCHEMA},
    )

    msg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.chat_threads.chat_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.app_users.user_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── Agent Runs & Events ────────────────────────────────────────────────────


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('started','streaming','succeeded','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        Index(
            "idx_agent_runs_idem",
            "chat_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        {"schema": _SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    chat_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.chat_threads.chat_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.app_users.user_id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    router_plan: Mapped[Optional[dict]] = mapped_column(JSONB)
    error: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_agent_events_run_seq"),
        Index("idx_agent_events_run", "run_id", "event_id"),
        {"schema": _SCHEMA},
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey(f"{_SCHEMA}.agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
