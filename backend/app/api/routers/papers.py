"""Paper ranking route."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import feedparser
import requests
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import (
    IndexArxivRequest,
    IndexArxivResponse,
    IndexedPaper,
    IndexedPaperSearchResponse,
    Paper,
    RankRequest,
    RankResponse,
)
from app.db.engine import get_session
from app.db.models import RPAbstractData
from app.db.repositories.papers import insert_papers, search_indexed_papers
from app.llm.doc_parser import DocumentIngestionPipeline
from app.settings import get_settings
from app.services.ranking import rank_papers
from app.services.retrieval import get_intent_vector
from app.utils.categories import get_category_name
from app.worker.tasks.summarize import summarize_chunks
from app.worker.tasks.vectors import update_intent_vectors

router = APIRouter(tags=["papers"])
logger = logging.getLogger(__name__)


def _extract_arxiv_id(url: str) -> str:
    url = url.strip()
    m = re.search(r"arxiv\.org\/(?:abs|pdf)\/([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)", url)
    if not m:
        raise ValueError("Invalid arXiv URL. Use links like https://arxiv.org/abs/2401.12345")
    return m.group(1)


def _to_row(entry: Any) -> dict[str, Any]:
    def _to_datetime(value: Any):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return None

    authors = [getattr(a, "name", "") for a in getattr(entry, "authors", []) if getattr(a, "name", None)]
    pdf_url = None
    for link in getattr(entry, "links", []):
        if getattr(link, "type", None) == "application/pdf":
            pdf_url = getattr(link, "href", None)
            break
    pc = getattr(entry, "arxiv_primary_category", None)
    primary_category = pc.get("term") if isinstance(pc, dict) else getattr(pc, "term", None)
    cats = []
    for t in getattr(entry, "tags", []):
        term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
        if term:
            cats.append(term)

    return {
        "title": getattr(entry, "title", None),
        "summary": getattr(entry, "summary", None),
        "authors": ", ".join(authors),
        "published": _to_datetime(getattr(entry, "published", None)),
        "updated": _to_datetime(getattr(entry, "updated", None)),
        "link": getattr(entry, "link", None),
        "pdf_url": pdf_url,
        "primary_category": primary_category,
        "all_categories": ", ".join(cats),
        "doi": getattr(entry, "arxiv_doi", None),
        "journal_ref": getattr(entry, "arxiv_journal_ref", None),
        "comment": getattr(entry, "arxiv_comment", None),
    }


@router.get("/papers", response_model=IndexedPaperSearchResponse)
async def list_indexed_papers(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> IndexedPaperSearchResponse:
    """Search indexed papers using keyword matching over title/summary/authors."""
    async with get_session() as session:
        async with session.begin():
            rows = await search_indexed_papers(session, q=q, category=category, limit=limit)

    return IndexedPaperSearchResponse(
        results=[
            IndexedPaper(
                paper_id=str(r["id"]),
                title=r["title"],
                summary=r.get("summary"),
                authors=r.get("authors"),
                categories=r.get("primary_category"),
                category_name=get_category_name(r.get("primary_category")),
                submitted_at=r.get("published"),
                link=r.get("link"),
                pdf_url=r.get("pdf_url"),
            )
            for r in rows
        ]
    )


@router.post("/papers/index-arxiv", response_model=IndexArxivResponse)
async def index_arxiv_paper(req: IndexArxivRequest) -> IndexArxivResponse:
    try:
        arxiv_id = _extract_arxiv_id(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    s = get_settings()
    query = f"id_list={arxiv_id}"
    resp = requests.get(
        s.paper_api_base_url,
        params={"search_query": query, "start": 0, "max_results": 1},
        timeout=s.paper_api_http_timeout,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch arXiv metadata")

    entries = feedparser.parse(resp.text).entries or []
    if not entries:
        raise HTTPException(status_code=404, detail="Paper not found on arXiv")

    row = _to_row(entries[0])
    async with get_session() as session:
        async with session.begin():
            await insert_papers(session, [row])

    # Run ingestion + summaries + vectors for newly inserted/unprocessed rows.
    await DocumentIngestionPipeline().run()
    summarize_chunks.delay()
    update_intent_vectors.delay()

    # Return the indexed paper id.
    async with get_session() as session:
        async with session.begin():
            row = (
                await session.execute(select(RPAbstractData.id).where(RPAbstractData.link == row["link"]))
            ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=500, detail="Paper indexed but lookup failed")

    return IndexArxivResponse(paper_id=str(row), status="indexed")


@router.post("/rank", response_model=RankResponse)
async def rank_endpoint(req: RankRequest) -> RankResponse:
    try:
        intent_vec = await get_intent_vector(req.query, req.category)
        rows = await rank_papers(
            intent_vectors=intent_vec,
            window_days=req.window_days,
            categories=req.category,
            top_k=req.top_k,
        )
        return RankResponse(
            results=[
                Paper(
                    paper_id=str(r["id"]),
                    title=r["title"],
                    summary=r["summary"],
                    categories=r["primary_category"],
                    submitted_at=r["created_at"],
                    score=float(r["score"]),
                )
                for r in rows
            ]
        )
    except ValueError as e:
        logger.warning("rank: %s — returning empty results", e)
        return RankResponse(results=[])
