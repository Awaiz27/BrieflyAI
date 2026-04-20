"""Repository for research paper CRUD operations."""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import bindparam, exists, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, PaperAbstractChunk, RPAbstractData

logger = logging.getLogger(__name__)


async def insert_papers(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = pg_insert(RPAbstractData).values(rows).on_conflict_do_nothing(index_elements=["link"])
    await session.execute(stmt)
    logger.info("Upserted %d paper rows", len(rows))


async def fetch_papers_by_interval(
    session: AsyncSession,
    value: int,
    unit: Literal["day", "week", "month"],
) -> list:
    interval_expr = text(f"INTERVAL '{value} {unit}'")
    stmt = (
        select(RPAbstractData.id, RPAbstractData.pdf_url, RPAbstractData.created_at)
        .where(RPAbstractData.created_at >= func.now() - interval_expr)
        .order_by(RPAbstractData.created_at)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def fetch_unprocessed_papers(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(
            RPAbstractData.id,
            RPAbstractData.pdf_url,
            RPAbstractData.created_at,
            RPAbstractData.title,
            RPAbstractData.summary,
        )
        .where(~exists(select(1).where(Chunk.rp_abstract_id == RPAbstractData.id)))
        .order_by(RPAbstractData.created_at.asc())
    )
    result = await session.execute(stmt)
    return result.mappings().all()  # type: ignore[return-value]


async def insert_chunks(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    await session.execute(insert(Chunk), rows)
    logger.info("Inserted %d body chunks", len(rows))


async def insert_abstract_chunks(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    await session.execute(insert(PaperAbstractChunk), rows)
    logger.info("Inserted %d abstract chunks", len(rows))


async def fetch_unsummarised_abstract_chunks(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(PaperAbstractChunk.id, PaperAbstractChunk.text)
        .where(PaperAbstractChunk.llm_summary.is_(None))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return result.mappings().all()  # type: ignore[return-value]


async def update_abstract_chunk_summaries(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = (
        update(PaperAbstractChunk)
        .where(PaperAbstractChunk.id == bindparam("id"))
        .values(llm_summary=bindparam("llm_summary"))
        .execution_options(synchronize_session=False)
    )
    await session.execute(stmt, rows)
    logger.info("Updated %d abstract chunk summaries", len(rows))


async def search_indexed_papers(
    session: AsyncSession,
    *,
    q: str | None,
    category: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.authors,
            RPAbstractData.primary_category,
            RPAbstractData.published,
            RPAbstractData.link,
            RPAbstractData.pdf_url,
        )
        .where(RPAbstractData.is_deleted.is_(False))
        .order_by(RPAbstractData.created_at.desc())
        .limit(limit)
    )

    if q:
        like_q = f"%{q}%"
        stmt = stmt.where(
            (RPAbstractData.title.ilike(like_q))
            | (RPAbstractData.summary.ilike(like_q))
            | (RPAbstractData.authors.ilike(like_q))
        )

    if category:
        stmt = stmt.where(RPAbstractData.primary_category == category)

    rows = (await session.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]


async def get_indexed_papers_by_ids(
    session: AsyncSession,
    *,
    paper_ids: list[str],
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []

    stmt = (
        select(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.authors,
            RPAbstractData.primary_category,
            RPAbstractData.published,
            RPAbstractData.link,
            RPAbstractData.pdf_url,
        )
        .where(RPAbstractData.id.in_(paper_ids), RPAbstractData.is_deleted.is_(False))
    )
    rows = (await session.execute(stmt)).mappings().all()

    # Preserve the caller's id order for stable UI chips.
    by_id = {str(r["id"]): dict(r) for r in rows}
    return [by_id[pid] for pid in paper_ids if pid in by_id]
