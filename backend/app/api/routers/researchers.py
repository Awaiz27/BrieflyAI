"""Researcher listing route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.api.schemas import ResearcherResult
from app.db.engine import get_session
from app.db.models import RPAbstractData

router = APIRouter(tags=["researchers"])


@router.get("/researchers", response_model=list[ResearcherResult])
async def list_researchers(
    q: str = Query(default="", description="Search term"),
    limit: int = Query(default=20, ge=1, le=100),
    _user_id: str = Depends(get_current_user),
) -> list[ResearcherResult]:
    async with get_session() as db:
        async with db.begin():
            stmt = (
                select(func.unnest(func.string_to_array(RPAbstractData.authors, ", ")).label("name"))
                .where(RPAbstractData.authors.isnot(None))
                .distinct()
                .limit(limit)
            )
            if q:
                stmt = stmt.where(RPAbstractData.authors.ilike(f"%{q}%"))
            rows = (await db.execute(stmt)).all()
    return [ResearcherResult(name=r[0]) for r in rows if r[0]]
