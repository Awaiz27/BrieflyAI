from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, String, literal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import timedelta
from db.models import RPAbstractData, intentVector, paperAbstractChunk
from db.engine.sql_engine import get_async_session
from configs.constants import CATEGORY_LOOKBACK_DAYS, GLOBAL_LOOKBACK_DAYS, MIN_CHUNKS_PER_CATEGORY
import logging
from typing import List

logger = logging.getLogger(__name__)


async def fetch_all_categories(session: AsyncSession) -> List[str]:
    stmt = (
        select(func.distinct(RPAbstractData.primary_category))
        .where(RPAbstractData.primary_category.isnot(None))
    )
    result = await session.execute(stmt)
    return [row[0] for row in result]


async def upsert_category_intent(
    session: AsyncSession,
    category: str,
    lookback_days: int,
) -> bool:

    subquery = (
        select(
            func.avg(paperAbstractChunk.embedding).label("embedding")
        )
        .select_from(paperAbstractChunk)
        .join(
            RPAbstractData,
            RPAbstractData.id == paperAbstractChunk.rp_abstract_id,
        )
        .where(
            RPAbstractData.primary_category == category,
            RPAbstractData.created_at
            >= func.now() - timedelta(days=lookback_days),
        )
        .having(func.count() >= MIN_CHUNKS_PER_CATEGORY)
    )

    insert_stmt = pg_insert(intentVector).from_select(
        ["name", "embedding", "updated_at"],
        select(
            func.cast(category, String),
            subquery.c.embedding,
            func.now(),
        ),
    )

    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[intentVector.name],
        set_={
            "embedding": insert_stmt.excluded.embedding,
            "updated_at": func.now(),
        },
    )

    result = await session.execute(stmt)
    return result.rowcount > 0



async def upsert_global_intent(
    session: AsyncSession,
    lookback_days: int,
) -> None:

    subquery = (
        select(func.avg(paperAbstractChunk.embedding).label("embedding"))
        .select_from(paperAbstractChunk)
        .join(
            RPAbstractData,
            RPAbstractData.id == paperAbstractChunk.rp_abstract_id,
        )
        .where(
            RPAbstractData.created_at
            >= func.now() - timedelta(days=lookback_days)
        )
    )

    insert_stmt = pg_insert(intentVector).from_select(
        ["name", "embedding", "updated_at"],
        select(
            literal("global"),
            subquery.c.embedding,
            func.now(),
        ),
    )

    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[intentVector.name],
        set_={
            "embedding": insert_stmt.excluded.embedding,
            "updated_at": func.now(),
        },
    )

    await session.execute(stmt)

async def fetch_intent_from_db(name: List[str]) -> List[float]:
    async with get_async_session() as db_session:
        async with db_session.begin():
            stmt = (
                select(intentVector.embedding)
                .where(intentVector.name.in_(name))
            )

            embedding = await db_session.execute(stmt)
            # embedding = result.scalar_one_or_none()

            if embedding is None:
                raise ValueError(f"Missing intent vector: {name}")

            # pgvector returns a sequence-like object
            return list(embedding)

async def run_intent_vector_job():
    async with get_async_session() as db_session:
        async with db_session.begin():
            categories = await fetch_all_categories(db_session)

            updated = skipped = 0

            logger.info(
                "Intent job started | categories=%d",
                len(categories),
            )

            for category in categories:
                success = await upsert_category_intent(
                    db_session,
                    category,
                    CATEGORY_LOOKBACK_DAYS,
                )
                updated += int(success)
                skipped += int(not success)

            await upsert_global_intent(
                db_session,
                GLOBAL_LOOKBACK_DAYS,
            )

            logger.info(
                "Intent job finished | updated=%d skipped=%d + global",
                updated,
                skipped,
            )