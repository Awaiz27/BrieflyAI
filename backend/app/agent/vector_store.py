"""Multi-stage, hybrid RAG vector store with dual-table search (papers + chunks).

Supports:
  1. Vector similarity search (cosine distance on embeddings)
  2. Keyword-based FTS (full-text search with ranking)
  3. RRF (Reciprocal Rank Fusion) to combine results
  4. Intelligent routing to search papers (summaries), chunks (details), or both
"""

from __future__ import annotations

import uuid
import logging
from typing import Any, Literal
from enum import Enum
from dataclasses import dataclass

from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperAbstractChunk, RPAbstractData
from app.services.embeddings import embed_query


logger = logging.getLogger(__name__)


class SearchTarget(str, Enum):
    """Which table(s) to search."""
    PAPERS = "papers"  # Search paper summaries only
    CHUNKS = "chunks"  # Search chunk details only
    BOTH = "both"      # Search both papers and chunks (dual-stage)


@dataclass
class SearchResult:
    """Unified search result from either papers or chunks."""
    id: str
    source_type: Literal["paper", "chunk"]  # Whether from RPAbstractData or PaperAbstractChunk
    text: str          # The actual text (summary or chunk)
    title: str         # Paper title
    link: str | None
    authors: str | None
    similarity_score: float   # Vector similarity (0-1)
    fts_rank: float | None    # FTS ranking score (if applicable)
    combined_score: float     # RRF-fused score for ranking
    metadata: dict[str, Any]  # Additional metadata


class PgVectorStore:
    """Multi-stage hybrid search over papers and chunks with RRF fusion."""

    # System prompts for routing decisions
    ROUTING_SYSTEM_PROMPT = """You are a query router for a research paper RAG system.

## Available Search Targets:
1. **Papers Table (RP_abstract_data)**: Contains high-level paper metadata
   - Fields: title, summary (with vector embedding), authors, published date, categories
   - Best for: General paper discovery, broad topic overview, author/category filtering
   - Use when: User asks about "papers on X", "finding research about Y", "what papers", "which papers"

2. **Chunks Table (PaperAbstractChunk)**: Contains detailed chunks from paper abstracts
   - Fields: chunk text (with vector embedding), llm_summary (detailed summary), similarity scores
   - Best for: Specific technical details, precise quotes, methodology, findings
   - Use when: User asks "how do they X?", "explain method", "what's the approach", "technical details"

3. **Both Tables (Dual-Stage)**: Search papers FIRST to find relevant papers, then detailed chunks from those papers
   - Best for: In-depth research requiring both overview and details
   - Use when: User asks open-ended research questions, needs comprehensive answers

## Decision Rules:
- If query is broad/categorical → PAPERS (for overview)
- If query is technical/specific → CHUNKS (for details)
- If query is research-heavy or complex → BOTH (dual-stage for comprehensive results)

Respond ONLY with valid JSON:
{
  "target": "papers" | "chunks" | "both",
  "reasoning": "brief explanation",
  "focus_fields": ["field1", "field2"]  // which fields to prioritize
}"""

    HYBRID_SEARCH_PROMPT = """Analyze this research query and determine the optimal search strategy.

Query: "{query}"

Consider:
1. Is this asking for paper discovery/overview (→ PAPERS)?
2. Is this asking for technical/implementation details (→ CHUNKS)?
3. Is this asking for comprehensive research understanding (→ BOTH)?

For PAPERS search, prioritize: title + summary
For CHUNKS search, prioritize: detailed chunk text + methodology
For BOTH search, use papers to filter relevant papers first, then get detailed chunks from those.

Respond with JSON:
{{
  "target": "papers|chunks|both",
  "search_strategy": "vector|hybrid|keyword",
  "explanation": "why this choice"
}}"""

    def __init__(self, k: int = 10, rrf_constant: float = 60.0):
        """Initialize vector store.
        
        Args:
            k: Number of results to return per search
            rrf_constant: RRF constant (higher = less weight to position difference)
        """
        self.k = k
        self.rrf_constant = rrf_constant

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        k: int | None = None,
        target: SearchTarget = SearchTarget.BOTH,
        paper_ids: list[str] | None = None,
        use_hybrid: bool = True,
    ) -> list[SearchResult]:
        """Multi-stage hybrid search.
        
        Args:
            session: Async database session
            query: User search query
            k: Number of results (defaults to self.k)
            target: Which table(s) to search
            paper_ids: Restrict to specific papers (optional)
            use_hybrid: If True, combine vector + keyword search; if False, vector only
            
        Returns:
            List of SearchResult objects ranked by combined RRF score
        """
        k = k or self.k
        
        results_by_source: dict[tuple[str, str], SearchResult] = {}  # (source_type, id) -> result
        
        # Route based on target
        if target == SearchTarget.PAPERS:
            return await self._search_papers(session, query, k, paper_ids, use_hybrid)
        
        elif target == SearchTarget.CHUNKS:
            return await self._search_chunks(session, query, k, paper_ids, use_hybrid)
        
        elif target == SearchTarget.BOTH:
            # Dual-stage: search papers first, then chunks from those papers
            logger.info(f"[PgVectorStore] Dual-stage search for query: {query}")
            
            # Stage 1: Find relevant papers
            paper_results = await self._search_papers(
                session, query, k=k, paper_ids=paper_ids, use_hybrid=use_hybrid
            )
            
            if not paper_results:
                logger.warning(f"[PgVectorStore] No papers found; returning empty results")
                return []
            
            # Extract paper IDs from results
            found_paper_ids = [r.metadata.get("rp_abstract_id") for r in paper_results if r.metadata.get("rp_abstract_id")]
            
            # Stage 2: Get detailed chunks from those papers
            chunk_results = await self._search_chunks(
                session, query, k=k, paper_ids=found_paper_ids, use_hybrid=use_hybrid
            )

            # Merge and rerank paper-level + chunk-level lists with proper RRF.
            return self._rrf_fuse_ranked_lists([paper_results, chunk_results], k=k)
        
        return []

    async def _search_papers(
        self,
        session: AsyncSession,
        query: str,
        k: int,
        paper_ids: list[str] | None = None,
        use_hybrid: bool = True,
    ) -> list[SearchResult]:
        """Search paper summaries with optional hybrid (vector + keyword) search."""
        logger.info(f"[PgVectorStore] Searching papers for: {query}")
        
        query_vec = await embed_query(query)

        if use_hybrid:
            # Vector search on paper summaries
            vector_results = await self._vector_search_papers(
                session, query_vec, k, paper_ids
            )
            
            # Keyword FTS search on paper summaries
            keyword_results = await self._keyword_search_papers(
                session, query, k, paper_ids
            )
            
            # Proper RRF fusion: combine independently ranked retrieval lists.
            return self._rrf_fuse_ranked_lists([vector_results, keyword_results], k=k)
        else:
            # Vector search only
            return await self._vector_search_papers(session, query_vec, k, paper_ids)

    async def _search_chunks(
        self,
        session: AsyncSession,
        query: str,
        k: int,
        paper_ids: list[str] | None = None,
        use_hybrid: bool = True,
    ) -> list[SearchResult]:
        """Search chunk details with optional hybrid (vector + keyword) search."""
        logger.info(f"[PgVectorStore] Searching chunks for: {query}")
        
        query_vec = await embed_query(query)

        if use_hybrid:
            # Vector search on chunks
            vector_results = await self._vector_search_chunks(
                session, query_vec, k, paper_ids
            )
            
            # Keyword FTS search on chunks
            keyword_results = await self._keyword_search_chunks(
                session, query, k, paper_ids
            )
            
            # Proper RRF fusion: combine independently ranked retrieval lists.
            return self._rrf_fuse_ranked_lists([vector_results, keyword_results], k=k)
        else:
            # Vector search only
            return await self._vector_search_chunks(session, query_vec, k, paper_ids)

    async def _vector_search_papers(
        self,
        session: AsyncSession,
        query_vec: list[float],
        k: int,
        paper_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Vector similarity search on paper summary embeddings."""
        stmt = (
            select(
                RPAbstractData.id,
                RPAbstractData.title,
                RPAbstractData.summary,
                RPAbstractData.authors,
                RPAbstractData.link,
                RPAbstractData.pdf_url,
                RPAbstractData.primary_category,
                RPAbstractData.published,
                (1.0 - RPAbstractData.summary_embedding.cosine_distance(query_vec)).label("similarity"),
            )
            .where(RPAbstractData.summary_embedding.isnot(None))
            .order_by(RPAbstractData.summary_embedding.cosine_distance(query_vec).asc())
            .limit(k)
        )
        
        if paper_ids:
            valid_ids = self._validate_paper_ids(paper_ids)
            if not valid_ids:
                return []
            stmt = stmt.where(RPAbstractData.id.in_(valid_ids))
        
        rows = (await session.execute(stmt)).mappings().all()
        
        results = []
        for i, row in enumerate(rows, 1):
            results.append(SearchResult(
                id=str(row["id"]),
                source_type="paper",
                text=row["summary"] or "",
                title=row["title"],
                link=row["link"],
                authors=row["authors"],
                similarity_score=float(row["similarity"]),
                fts_rank=None,
                combined_score=1.0 / (i + self.rrf_constant),  # RRF rank
                metadata={
                    "rp_abstract_id": str(row["id"]),
                    "primary_category": row["primary_category"],
                    "published": row["published"],
                    "pdf_url": row["pdf_url"],
                },
            ))
        
        return results

    async def _vector_search_chunks(
        self,
        session: AsyncSession,
        query_vec: list[float],
        k: int,
        paper_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Vector similarity search on chunk embeddings."""
        stmt = (
            select(
                PaperAbstractChunk.id,
                PaperAbstractChunk.rp_abstract_id,
                PaperAbstractChunk.text,
                PaperAbstractChunk.llm_summary,
                RPAbstractData.title,
                RPAbstractData.link,
                RPAbstractData.authors,
                (1.0 - PaperAbstractChunk.embedding.cosine_distance(query_vec)).label("similarity"),
            )
            .join(RPAbstractData, RPAbstractData.id == PaperAbstractChunk.rp_abstract_id)
            .order_by(PaperAbstractChunk.embedding.cosine_distance(query_vec).asc())
            .limit(k)
        )
        
        if paper_ids:
            valid_ids = self._validate_paper_ids(paper_ids)
            if not valid_ids:
                return []
            stmt = stmt.where(RPAbstractData.id.in_(valid_ids))
        
        rows = (await session.execute(stmt)).mappings().all()
        
        results = []
        for i, row in enumerate(rows, 1):
            results.append(SearchResult(
                id=str(row["id"]),
                source_type="chunk",
                text=row["llm_summary"] or row["text"],  # Prefer summary if available
                title=row["title"],
                link=row["link"],
                authors=row["authors"],
                similarity_score=float(row["similarity"]),
                fts_rank=None,
                combined_score=1.0 / (i + self.rrf_constant),  # RRF rank
                metadata={
                    "rp_abstract_id": str(row["rp_abstract_id"]),
                    "chunk_id": str(row["id"]),
                    "full_text": row["text"],
                },
            ))
        
        return results

    async def _keyword_search_papers(
        self,
        session: AsyncSession,
        query: str,
        k: int,
        paper_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Keyword search on title+summary+authors with trigram fallback."""
        ts_query = func.websearch_to_tsquery("english", query)
        searchable_doc = (
            func.setweight(
                func.to_tsvector("english", func.coalesce(RPAbstractData.title, "")),
                text("'A'::\"char\""),
            )
            .op("||")(
                func.setweight(
                    func.to_tsvector("english", func.coalesce(RPAbstractData.summary, "")),
                    text("'B'::\"char\""),
                )
            )
            .op("||")(
                func.setweight(
                    func.to_tsvector("english", func.coalesce(RPAbstractData.authors, "")),
                    text("'C'::\"char\""),
                )
            )
        )

        stmt = (
            select(
                RPAbstractData.id,
                RPAbstractData.title,
                RPAbstractData.summary,
                RPAbstractData.authors,
                RPAbstractData.link,
                RPAbstractData.pdf_url,
                RPAbstractData.primary_category,
                RPAbstractData.published,
                func.ts_rank(
                    searchable_doc,
                    ts_query,
                ).label("fts_rank"),
            )
            .where(searchable_doc.op("@@")(ts_query))
            .order_by(text("fts_rank DESC"), RPAbstractData.published.desc())
            .limit(k)
        )
        
        if paper_ids:
            valid_ids = self._validate_paper_ids(paper_ids)
            if not valid_ids:
                return []
            stmt = stmt.where(RPAbstractData.id.in_(valid_ids))
        
        rows = (await session.execute(stmt)).mappings().all()

        # Fallback for typo-heavy queries or FTS misses.
        if not rows:
            fallback_stmt = (
                select(
                    RPAbstractData.id,
                    RPAbstractData.title,
                    RPAbstractData.summary,
                    RPAbstractData.authors,
                    RPAbstractData.link,
                    RPAbstractData.pdf_url,
                    RPAbstractData.primary_category,
                    RPAbstractData.published,
                    func.greatest(
                        func.similarity(func.coalesce(RPAbstractData.title, ""), query),
                        func.similarity(func.coalesce(RPAbstractData.summary, ""), query),
                    ).label("fts_rank"),
                )
                .where(
                    or_(
                        func.similarity(func.coalesce(RPAbstractData.title, ""), query) > 0.08,
                        func.similarity(func.coalesce(RPAbstractData.summary, ""), query) > 0.08,
                    )
                )
                .order_by(text("fts_rank DESC"), RPAbstractData.published.desc())
                .limit(k)
            )
            if paper_ids:
                valid_ids = self._validate_paper_ids(paper_ids)
                if not valid_ids:
                    return []
                fallback_stmt = fallback_stmt.where(RPAbstractData.id.in_(valid_ids))
            rows = (await session.execute(fallback_stmt)).mappings().all()
        
        results = []
        for i, row in enumerate(rows, 1):
            results.append(SearchResult(
                id=str(row["id"]),
                source_type="paper",
                text=row["summary"] or "",
                title=row["title"],
                link=row["link"],
                authors=row["authors"],
                similarity_score=0.0,  # Not from vector search
                fts_rank=float(row["fts_rank"]),
                combined_score=1.0 / (i + self.rrf_constant),  # RRF rank
                metadata={
                    "rp_abstract_id": str(row["id"]),
                    "primary_category": row["primary_category"],
                    "published": row["published"],
                    "pdf_url": row["pdf_url"],
                },
            ))
        
        return results

    async def _keyword_search_chunks(
        self,
        session: AsyncSession,
        query: str,
        k: int,
        paper_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Keyword-based FTS search on chunk text."""
        stmt = (
            select(
                PaperAbstractChunk.id,
                PaperAbstractChunk.rp_abstract_id,
                PaperAbstractChunk.text,
                PaperAbstractChunk.llm_summary,
                RPAbstractData.title,
                RPAbstractData.link,
                RPAbstractData.authors,
                func.ts_rank(
                    PaperAbstractChunk.fts,
                    func.plainto_tsquery("english", query)
                ).label("fts_rank"),
            )
            .join(RPAbstractData, RPAbstractData.id == PaperAbstractChunk.rp_abstract_id)
            .where(PaperAbstractChunk.fts.op("@@")(func.plainto_tsquery("english", query)))
            .order_by(text("fts_rank DESC"))
            .limit(k)
        )
        
        if paper_ids:
            valid_ids = self._validate_paper_ids(paper_ids)
            if not valid_ids:
                return []
            stmt = stmt.where(RPAbstractData.id.in_(valid_ids))
        
        rows = (await session.execute(stmt)).mappings().all()
        
        results = []
        for i, row in enumerate(rows, 1):
            results.append(SearchResult(
                id=str(row["id"]),
                source_type="chunk",
                text=row["llm_summary"] or row["text"],
                title=row["title"],
                link=row["link"],
                authors=row["authors"],
                similarity_score=0.0,  # Not from vector search
                fts_rank=float(row["fts_rank"]),
                combined_score=1.0 / (i + self.rrf_constant),  # RRF rank
                metadata={
                    "rp_abstract_id": str(row["rp_abstract_id"]),
                    "chunk_id": str(row["id"]),
                    "full_text": row["text"],
                },
            ))
        
        return results

    def _rrf_fuse_ranked_lists(
        self,
        ranked_lists: list[list[SearchResult]],
        k: int,
    ) -> list[SearchResult]:
        """Reciprocal Rank Fusion over independently ranked result lists.

        For each list, score contribution is:
            1 / (rrf_constant + rank)
        """
        fused: dict[str, SearchResult] = {}
        scores: dict[str, float] = {}

        for ranked in ranked_lists:
            for rank, result in enumerate(ranked, start=1):
                key = result.id
                score = 1.0 / (self.rrf_constant + rank)

                if key not in fused:
                    fused[key] = result
                    scores[key] = 0.0
                else:
                    existing = fused[key]
                    existing.similarity_score = max(existing.similarity_score, result.similarity_score)
                    if result.fts_rank is not None:
                        if existing.fts_rank is None:
                            existing.fts_rank = result.fts_rank
                        else:
                            existing.fts_rank = max(existing.fts_rank, result.fts_rank)

                scores[key] += score

        for key, result in fused.items():
            result.combined_score = scores[key]

        sorted_results = sorted(fused.values(), key=lambda r: r.combined_score, reverse=True)
        return sorted_results[:k]

    @staticmethod
    def _validate_paper_ids(paper_ids: list[str]) -> list[uuid.UUID]:
        """Validate and convert paper IDs to UUIDs."""
        valid_ids: list[uuid.UUID] = []
        for pid in paper_ids:
            try:
                valid_ids.append(uuid.UUID(pid))
            except (TypeError, ValueError):
                logger.warning(f"[PgVectorStore] Invalid paper UUID: {pid}")
                continue
        return valid_ids
