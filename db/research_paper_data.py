from typing import List, Literal
from sqlalchemy import insert, select, text, func, exists, update, bindparam
from db.models import RPAbstractData, Chunk, paperAbstractChunk
from db.engine.sql_engine import get_async_session
import logging

logger = logging.getLogger(__name__)

async def insert_rp_data(rows : List[dict]):
    logger.info("Start adding rows into the RPAbstractData Table")
    async with get_async_session() as db_session:
        async with db_session.begin():
            await db_session.execute(insert(RPAbstractData), rows)

async def get_rp_data_by_db_interval(
    value: int,
    unit: Literal["day", "week", "month"]
):
    """
    Fetch RPAbstractData using DB-side time calculation (PostgreSQL).
    """

    interval_expr = text(f"INTERVAL '{value} {unit}'")

    stmt = (
        select(RPAbstractData.id, RPAbstractData.pdf_url, RPAbstractData.created_at)
        .where(RPAbstractData.created_at >= func.now() - interval_expr)
        .order_by(RPAbstractData.created_at) #.desc()
    )


    async with get_async_session() as db_session:
        async with db_session.begin():
            result = await db_session.execute(stmt)
    return result.scalars().all()


async def fetch_unprocessed_paper() -> List[dict]:
        """
        Fetch RPAbstractData rows that do NOT yet have chunks.
        """

        stmt = (
            select(
                RPAbstractData.id,
                RPAbstractData.pdf_url,
                RPAbstractData.created_at,
                RPAbstractData.title, 
                RPAbstractData.summary, 
            )
            .where(
                ~exists(
                    select(1).where(
                        Chunk.rp_abstract_id == RPAbstractData.id
                    )
                )
            )
            .order_by(RPAbstractData.created_at.asc())
        )

        async with get_async_session() as db_session:
            async with db_session.begin():
                result = await db_session.execute(stmt)

        return result.mappings().all()


async def insert_paper_chunk_data(rows : List[dict]):
    logger.info("Start adding rows into the Paper Chunk Table")
    async with get_async_session() as db_session:
        async with db_session.begin():
            await db_session.execute(insert(Chunk), rows)
    logger.info(f"{len(rows)} rows added into the Paper Chunk Table")


async def insert_abstract_chunk_data(rows : List[dict]):
    logger.info("Start adding rows into the Abstract Chunk Table")
    async with get_async_session() as db_session:
        async with db_session.begin():
            await db_session.execute(insert(paperAbstractChunk), rows)
    logger.info(f"{len(rows)} rows added into the Abstract Chunk Table")


async def fetch_paper_abstract_chunk_batch(
    limit: int
) -> list[dict]:
    stmt = (
        select(
            paperAbstractChunk.id,
            paperAbstractChunk.text
        )
        .where(paperAbstractChunk.llm_summary.is_(None))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    async with get_async_session() as db_session:
        async with db_session.begin():
            result = await db_session.execute(stmt)
            return result.mappings().all()

async def update_abstract_chunk_summaries(rows: list[dict]):
    # stmt = (
    #     update(paperAbstractChunk)
    #     .where(paperAbstractChunk.id == bindparam("chunk_id"))
    #     .values(llm_summary=bindparam("llm_chunk_summary"))
    # )

    stmt = (
        update(paperAbstractChunk)
        .values(llm_summary=bindparam("llm_summary"))
        .execution_options(synchronize_session=None)
        )

    async with get_async_session() as db_session:
        async with db_session.begin():
            await db_session.execute(stmt.execution_options(synchronize_session=None), rows)
