"""Repository for intent vector operations."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import String, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IntentVector, PaperAbstractChunk, RPAbstractData
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def fetch_all_categories(session: AsyncSession) -> list[str]:
    stmt = select(func.distinct(RPAbstractData.primary_category)).where(
        RPAbstractData.primary_category.isnot(None)
    )
    result = await session.execute(stmt)
    return [row[0] for row in result]


async def upsert_category_intent(
    session: AsyncSession,
    category: str,
    lookback_days: int,
) -> bool:
    s = get_settings()
    subquery = (
        select(func.avg(PaperAbstractChunk.embedding).label("embedding"))
        .select_from(PaperAbstractChunk)
        .join(RPAbstractData, RPAbstractData.id == PaperAbstractChunk.rp_abstract_id)
        .where(
            RPAbstractData.primary_category == category,
            RPAbstractData.created_at >= func.now() - timedelta(days=lookback_days),
        )
        .having(func.count() >= s.min_chunks_per_category)
    )

    insert_stmt = pg_insert(IntentVector).from_select(
        ["name", "embedding", "updated_at"],
        select(func.cast(category, String), subquery.c.embedding, func.now()),
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[IntentVector.name],
        set_={"embedding": insert_stmt.excluded.embedding, "updated_at": func.now()},
    )
    result = await session.execute(stmt)
    return result.rowcount > 0


async def upsert_global_intent(session: AsyncSession, lookback_days: int) -> None:
    subquery = (
        select(func.avg(PaperAbstractChunk.embedding).label("embedding"))
        .select_from(PaperAbstractChunk)
        .join(RPAbstractData, RPAbstractData.id == PaperAbstractChunk.rp_abstract_id)
        .where(RPAbstractData.created_at >= func.now() - timedelta(days=lookback_days))
    )
    insert_stmt = pg_insert(IntentVector).from_select(
        ["name", "embedding", "updated_at"],
        select(literal("global"), subquery.c.embedding, func.now()),
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[IntentVector.name],
        set_={"embedding": insert_stmt.excluded.embedding, "updated_at": func.now()},
    )
    await session.execute(stmt)


async def fetch_intent_vectors(session: AsyncSession, names: list[str]) -> list[list[float]]:
    stmt = select(IntentVector.embedding).where(IntentVector.name.in_(names))
    result = await session.execute(stmt)
    vectors = result.scalars().all()
    if not vectors:
        raise ValueError(f"Missing intent vectors: {names}")
    return [list(v) for v in vectors]


async def run_intent_vector_job(session: AsyncSession) -> None:
    s = get_settings()
    categories = await fetch_all_categories(session)
    updated = skipped = 0
    logger.info("Intent vector job started | categories=%d", len(categories))

    for cat in categories:
        success = await upsert_category_intent(session, cat, s.category_lookback_days)
        updated += int(success)
        skipped += int(not success)

    await upsert_global_intent(session, s.global_lookback_days)
    logger.info("Intent vector job finished | updated=%d skipped=%d + global", updated, skipped)
