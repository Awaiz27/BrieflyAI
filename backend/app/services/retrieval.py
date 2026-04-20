"""Intent vector retrieval and query blending."""

from __future__ import annotations

from typing import Optional

from app.db.engine import get_session
from app.db.repositories.vectors import fetch_intent_vectors
from app.services.embeddings import blend_vectors, embed_query


async def get_intent_vector(
    query: Optional[str],
    category: Optional[list[str]],
) -> list[list[float]]:
    if query and category:
        qv = await embed_query(query)
        async with get_session() as session:
            async with session.begin():
                cvs = await fetch_intent_vectors(session, category)
        return [blend_vectors(qv, cv, alpha=0.7) for cv in cvs]

    if query:
        return [await embed_query(query)]

    async with get_session() as session:
        async with session.begin():
            if category:
                return await fetch_intent_vectors(session, category)
            return await fetch_intent_vectors(session, ["global"])
