"""Paper ranking service using vector similarity + recency."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.engine import get_session
from app.db.models import PaperAbstractChunk, RPAbstractData
from app.settings import get_settings
from app.utils.diversity import simple_dedup_by_title

logger = logging.getLogger(__name__)


def _normalise_vectors(vectors: list[list[float]]) -> list[list[float]]:
    return [[float(x) for x in v] for v in vectors if v]


async def rank_papers(
    intent_vectors: list[list[float]],
    window_days: int,
    categories: Optional[list[str]],
    top_k: int,
) -> list[dict[str, Any]]:
    s = get_settings()
    intent_vectors = _normalise_vectors(intent_vectors)
    if not intent_vectors:
        raise ValueError("At least one intent vector is required")

    top_k = min(top_k, s.max_top_k)
    tau_hours = max(6.0, float(window_days) * 12.0)

    # chunk-level similarity — MAX over intents
    sim_exprs = [
        1.0 - PaperAbstractChunk.embedding.cosine_distance(v) for v in intent_vectors
    ]
    combined_sim = func.greatest(*sim_exprs).label("similarity")

    chunk_sub = (
        select(PaperAbstractChunk.rp_abstract_id.label("paper_id"), combined_sim)
        .order_by(combined_sim.desc())
        .limit(s.chunk_recall_limit)
        .subquery()
    )

    paper_sim = func.max(chunk_sub.c.similarity)
    recency = s.recency_weight * func.exp(
        -func.extract("epoch", func.now() - RPAbstractData.created_at) / (tau_hours * 3600.0)
    )
    score = (paper_sim + recency).label("score")

    stmt = (
        select(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.primary_category,
            RPAbstractData.created_at,
            score,
        )
        .join(chunk_sub, chunk_sub.c.paper_id == RPAbstractData.id)
        .where(RPAbstractData.created_at >= func.now() - timedelta(days=window_days))
        .group_by(
            RPAbstractData.id,
            RPAbstractData.title,
            RPAbstractData.summary,
            RPAbstractData.primary_category,
            RPAbstractData.created_at,
        )
        .order_by(score.desc())
        .limit(top_k * 2)
    )

    if categories:
        stmt = stmt.where(RPAbstractData.primary_category.in_(categories))

    async with get_session() as session:
        async with session.begin():
            rows = (await session.execute(stmt)).mappings().all()

    return simple_dedup_by_title([dict(r) for r in rows], k=top_k)
