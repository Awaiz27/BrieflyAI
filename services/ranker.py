from typing import Any, Dict, List, Optional
from datetime import timedelta
from sqlalchemy import (
    select,
    func,
    bindparam,
    literal,
)
from sqlalchemy.ext.asyncio import AsyncSession
from pgvector.sqlalchemy import Vector

from db.engine.sql_engine import get_async_session
from db.models import RPAbstractData
from db.models import paperAbstractChunk
from utils.diversity import simple_dedup_by_title
from configs.constants import MAX_TOP_K, CHUNK_RECALL_LIMIT, RECENCY_WEIGHT


def normalize_intent_vectors(vectors: list[list[float]]) -> list[list[float]]:
    return [
        [float(x) for x in vec[0]]
        for vec in vectors
        if vec
    ]


async def rank_papers(
    intent_vectors: List[List[float]],
    window_days: int,
    categories: Optional[List[str]],
    top_k: int,
) -> List[Dict[str, Any]]:

    # -------------------------
    # Guardrails
    # -------------------------
    intent_vectors = normalize_intent_vectors(intent_vectors)
    if not intent_vectors:
        raise ValueError("At least one intent vector is required")

    top_k = min(top_k, MAX_TOP_K)
    tau_hours = max(6.0, float(window_days) * 12.0)
    recency_weight = 0.3

    # -------------------------
    # Chunk-level similarity
    # MAX over multiple intents
    # -------------------------
    similarity_exprs = [
        1.0 - paperAbstractChunk.embedding.cosine_distance(vec)
        for vec in intent_vectors
    ]

    combined_similarity = func.greatest(*similarity_exprs).label("similarity")

    # -------------------------
    # Phase 1: Recall chunks
    # -------------------------
    chunk_subquery = (
        select(
            paperAbstractChunk.rp_abstract_id.label("paper_id"),
            combined_similarity,
        )
        .order_by(combined_similarity.desc())
        .limit(CHUNK_RECALL_LIMIT)
        .subquery()
    )

    # -------------------------
    # Phase 2: Collapse to paper
    # -------------------------
    paper_similarity = func.max(chunk_subquery.c.similarity)

    recency_score = recency_weight * func.exp(
        -func.extract("epoch", func.now() - RPAbstractData.created_at)
        / (tau_hours * 3600.0)
    )

    score_expr = (paper_similarity + recency_score).label("score")

    # -------------------------
    # Final query
    # -------------------------
    stmt = (
        select(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.primary_category,
            RPAbstractData.created_at,
            score_expr,
        )
        .join(
            chunk_subquery,
            chunk_subquery.c.paper_id == RPAbstractData.id,
        )
        .where(
            RPAbstractData.created_at
            >= func.now() - timedelta(days=window_days)
        )
        .group_by(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.primary_category,
            RPAbstractData.created_at,
        )
        .order_by(score_expr.desc())
        .limit(top_k * 2)  # overfetch for diversity
    )

    # -------------------------
    # Category filter (multi)
    # -------------------------
    if categories:
        stmt = stmt.where(
            RPAbstractData.primary_category.in_(categories)
        )

    # -------------------------
    # Execute
    # -------------------------
    async with get_async_session() as session:
        async with session.begin():
            rows = (await session.execute(stmt)).mappings().all()

    # -------------------------
    # Dedup + trim
    # -------------------------
    return simple_dedup_by_title(
        [dict(r) for r in rows],
        k=top_k,
    )