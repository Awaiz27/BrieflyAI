"""LLM-callable tools for intelligent RAG search.

Tools convert manual vector store operations into callable instruments
that the LLM decides whether to invoke, enabling autonomous research.

## Database Relations

```
┌──────────────────────────────────────────────────────────────────┐
│                    Entity Relationships                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────┐                                     │
│  │   ChatThread (Chat)     │  ← User conversation context        │
│  ├─────────────────────────┤                                     │
│  │ chat_id (PK)            │                                     │
│  │ user_id                 │                                     │
│  │ focused_paper_ids (JSON)│ ─→ Filter research scope            │
│  │ rolling_summary         │ ─→ Conversation memory              │
│  └─────────────┬───────────┘                                     │
│                │ 1:N                                              │
│                ↓                                                   │
│  ┌─────────────────────────┐                                     │
│  │    ChatMessage (History)│  ← Query history for context        │
│  ├─────────────────────────┤                                     │
│  │ msg_id (PK)             │                                     │
│  │ chat_id (FK)            │                                     │
│  │ role (user/assistant)   │                                     │
│  │ content                 │                                     │
│  └─────────────────────────┘                                     │
│                                                                   │
│  ┌────────────────────────────────┐                              │
│  │   RP_abstract_data (Papers)    │ ← Paper metadata             │
│  ├────────────────────────────────┤                              │
│  │ id (PK, UUID)                  │                              │
│  │ title                          │                              │
│  │ summary + summary_embedding    │ ← Vector search              │
│  │ summary_fts                    │ ← Keyword search             │
│  │ authors, published, categories │                              │
│  │ link, doi, pdf_url             │                              │
│  └────────────────┬───────────────┘                              │
│                   │ 1:N (has many chunks)                        │
│                   ↓                                               │
│  ┌────────────────────────────────┐                              │
│  │ paperAbstractChunk (Chunks-A)  │ ← Abstract chunk content     │
│  ├────────────────────────────────┤                              │
│  │ id (PK, UUID)                  │                              │
│  │ rp_abstract_id (FK)            │ → Links to paper             │
│  │ text, llm_summary              │                              │
│  │ embedding + fts                │ ← Search indices             │
│  └────────────────────────────────┘                              │
│                                                                   │
│  ┌────────────────────────────────┐                              │
│  │   chunk_data (Chunks-B)        │ ← Full paper body chunks     │
│  ├────────────────────────────────┤                              │
│  │ id (PK, UUID)                  │                              │
│  │ rp_abstract_id (FK)            │ → Links to paper             │
│  │ doc_id, text, llm_summary      │                              │
│  │ section, page_start, page_end  │                              │
│  │ embedding + fts                │ ← Hybrid search indices      │
│  └────────────────────────────────┘                              │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘

## Table Descriptions

### ChatThread → ChatMessage (1:N)
- Stores conversation metadata and persists rolling summaries
- ChatMessages contain the actual messages for context
- focused_paper_ids: Researcher can scope search to specific papers
- rolling_summary: LLM-maintained summary for context inclusion

### RPAbstractData ← → paperAbstractChunk / chunk_data (1:N)
- RP_abstract_data stores paper-level metadata (title, summary, authors, links, categories)
- paperAbstractChunk stores abstract-focused chunks and llm_summary
- chunk_data stores detailed body text + section/page context
- Both chunk tables support embedding + FTS for hybrid retrieval

### Search Flow

User asks question in ChatThread
  ↓
Model decides which tool to call:
  ├─ search_papers() → Paper-level discovery (overview)
  ├─ search_chunks() → Chunk-level detail (technical Q&A)
  ├─ search_both() → Dual-stage (papers → chunks from those papers)
  ├─ search_history() → Conversation history (context)
  └─ combined_search() → Multi-source (papers + chunks + history)
  ↓
Tool executes SQL queries with vector/FTS/semantic search
  ↓
Results include source type, ranking scores, full metadata
  ↓
Model composes answer with citations from search results
```

## Tools Available

1. **search_papers** - Search paper summaries (discovery)
2. **search_chunks** - Search chunk details (technical)
3. **search_both** - Dual-stage search (comprehensive)
4. **search_history** - Query conversation history
5. **combined_search** - All sources at once
6. **analyze_focus_papers** - Analyze focused paper set

Each tool returns rich metadata for LLM to compose citations.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from dataclasses import dataclass

from langchain_core.tools import tool
from sqlalchemy import func, select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.vector_store import PgVectorStore, SearchTarget, SearchResult
from app.db.engine import get_session
from app.db.models import ChatMessage, ChatThread, Chunk, RPAbstractData, PaperAbstractChunk


logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Unified result from any search tool."""
    tool_name: str
    status: str  # "success" or "error"
    message: str
    results: list[dict[str, Any]]
    metadata: dict[str, Any]


class ResearchTools:
    """Collection of tools for intelligent RAG search."""

    def __init__(self, vector_store: Optional[PgVectorStore] = None):
        """Initialize research tools.
        
        Args:
            vector_store: Optional pre-configured PgVectorStore instance
        """
        self.vs = vector_store or PgVectorStore(k=10)

    async def search_papers(
        self,
        query: str,
        k: int = 10,
        paper_ids: Optional[list[str]] = None,
    ) -> ToolResult:
        """Search paper summaries for discovery and overview.
        
        Use this when the user is:
        - Looking for papers on a topic
        - Trying to discover relevant research
        - Asking "what papers exist on X?"
        - Want high-level overview before details
        
        Args:
            query: Research query
            k: Number of results (default: 10)
            paper_ids: Optional filter to specific papers
            
        Returns:
            ToolResult with ranked papers
        """
        logger.info(f"[search_papers] Query: {query[:80]}, k={k}")
        
        try:
            async with get_session() as session:
                async with session.begin():
                    results = await self.vs.search(
                        session,
                        query=query,
                        k=k,
                        target=SearchTarget.PAPERS,
                        paper_ids=paper_ids,
                        use_hybrid=True,
                    )
            
            result_dicts = [self._result_to_dict(r, include_full_text=False) for r in results]
            
            return ToolResult(
                tool_name="search_papers",
                status="success",
                message=f"Found {len(result_dicts)} papers matching '{query}'",
                results=result_dicts,
                metadata={
                    "search_type": "papers",
                    "query": query,
                    "count": len(result_dicts),
                    "filtered_by_papers": len(paper_ids) if paper_ids else 0,
                },
            )
        except Exception as e:
            logger.error(f"[search_papers] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="search_papers",
                status="error",
                message=f"Failed to search papers: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    async def search_chunks(
        self,
        query: str,
        k: int = 10,
        paper_ids: Optional[list[str]] = None,
    ) -> ToolResult:
        """Search chunk details for technical questions.
        
        Use this when the user is:
        - Asking for technical implementation details
        - Asking "how do they...?" or "what technique...?"
        - Want specific methodology or findings
        - Need direct quotes or precise answers
        
        Args:
            query: Research query
            k: Number of results (default: 10)
            paper_ids: Optional filter to specific papers
            
        Returns:
            ToolResult with ranked chunks
        """
        logger.info(f"[search_chunks] Query: {query[:80]}, k={k}")
        
        try:
            async with get_session() as session:
                async with session.begin():
                    results = await self.vs.search(
                        session,
                        query=query,
                        k=k,
                        target=SearchTarget.CHUNKS,
                        paper_ids=paper_ids,
                        use_hybrid=True,
                    )
            
            result_dicts = [self._result_to_dict(r, include_full_text=True) for r in results]
            
            return ToolResult(
                tool_name="search_chunks",
                status="success",
                message=f"Found {len(result_dicts)} relevant chunks for '{query}'",
                results=result_dicts,
                metadata={
                    "search_type": "chunks",
                    "query": query,
                    "count": len(result_dicts),
                    "filtered_by_papers": len(paper_ids) if paper_ids else 0,
                },
            )
        except Exception as e:
            logger.error(f"[search_chunks] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="search_chunks",
                status="error",
                message=f"Failed to search chunks: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    async def search_both(
        self,
        query: str,
        k: int = 10,
        paper_ids: Optional[list[str]] = None,
    ) -> ToolResult:
        """Dual-stage search: papers first, then detailed chunks from those papers.
        
        Use this when the user is:
        - Asking open-ended research questions
        - Want comprehensive understanding (overview + details)
        - Need both paper discovery AND technical depth
        - Asking "comprehensive overview of X"
        
        Process:
        1. Search papers to find relevant papers
        2. Extract paper IDs from results
        3. Search chunks from those papers only
        4. Merge and rank all results
        
        Args:
            query: Research query
            k: Number of results (default: 10)
            paper_ids: Optional filter to specific papers
            
        Returns:
            ToolResult with both papers and chunks
        """
        logger.info(f"[search_both] Dual-stage query: {query[:80]}, k={k}")
        
        try:
            async with get_session() as session:
                async with session.begin():
                    results = await self.vs.search(
                        session,
                        query=query,
                        k=k,
                        target=SearchTarget.BOTH,  # Dual-stage
                        paper_ids=paper_ids,
                        use_hybrid=True,
                    )
            
            result_dicts = [self._result_to_dict(r, include_full_text=True) for r in results]
            
            # Separate papers and chunks for metadata
            papers = [r for r in result_dicts if r.get("source_type") == "paper"]
            chunks = [r for r in result_dicts if r.get("source_type") == "chunk"]
            
            return ToolResult(
                tool_name="search_both",
                status="success",
                message=f"Found {len(papers)} papers and {len(chunks)} detailed chunks for '{query}'",
                results=result_dicts,
                metadata={
                    "search_type": "both",
                    "query": query,
                    "total_count": len(result_dicts),
                    "papers_count": len(papers),
                    "chunks_count": len(chunks),
                    "filtered_by_papers": len(paper_ids) if paper_ids else 0,
                },
            )
        except Exception as e:
            logger.error(f"[search_both] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="search_both",
                status="error",
                message=f"Failed to search papers and chunks: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    async def search_history(
        self,
        chat_id: str,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> ToolResult:
        """Search conversation history for context.
        
        Use this when:
        - Need to understand previous discussion
        - Want to check what was already discussed
        - Looking for context from earlier in conversation
        - Need to follow-up on previous answers
        
        Args:
            chat_id: Conversation ID to search in
            query: Optional keyword to search within history
            limit: Number of messages to return (default: 20)
            
        Returns:
            ToolResult with historical messages
        """
        logger.info(f"[search_history] Chat: {chat_id}, limit={limit}")
        
        try:
            async with get_session() as session:
                async with session.begin():
                    stmt = (
                        select(
                            ChatMessage.msg_id,
                            ChatMessage.role,
                            ChatMessage.content,
                            ChatMessage.created_at,
                        )
                        .where(ChatMessage.chat_id == chat_id)
                        .order_by(ChatMessage.msg_id.desc())
                        .limit(limit)
                    )
                    
                    rows = (await session.execute(stmt)).mappings().all()
                    
                    # If query provided, filter by keyword
                    if query:
                        filtered_rows = [
                            r for r in rows
                            if query.lower() in r["content"].lower()
                        ]
                        rows = filtered_rows
            
            result_dicts = [
                {
                    "msg_id": r["msg_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]
            
            # Sort by msg_id ascending for chronological order
            result_dicts.sort(key=lambda x: x["msg_id"])
            
            return ToolResult(
                tool_name="search_history",
                status="success",
                message=f"Retrieved {len(result_dicts)} messages from conversation history",
                results=result_dicts,
                metadata={
                    "search_type": "history",
                    "chat_id": chat_id,
                    "count": len(result_dicts),
                    "keyword_filtered": query is not None,
                },
            )
        except Exception as e:
            logger.error(f"[search_history] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="search_history",
                status="error",
                message=f"Failed to search history: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    async def combined_search(
        self,
        query: str,
        chat_id: str,
        k: int = 10,
        paper_ids: Optional[list[str]] = None,
        include_history: bool = True,
    ) -> ToolResult:
        """Combined search: papers + chunks + history.
        
        Use this when:
        - Want all available information at once
        - Need to understand full context and research
        - Combining conversation history with research findings
        - Want most comprehensive answer
        
        Process:
        1. Search papers (overview)
        2. Search chunks (details)
        3. Search history (context)
        4. Merge results with source tracking
        
        Args:
            query: Research query
            chat_id: Conversation ID for history
            k: Number of results per search (default: 10)
            paper_ids: Optional filter to specific papers
            include_history: Whether to include history (default: True)
            
        Returns:
            ToolResult with all sources combined
        """
        logger.info(f"[combined_search] Query: {query[:80]}, with history={include_history}")
        
        all_results = []
        metadata = {
            "search_type": "combined",
            "query": query,
            "sources": {},
        }
        
        try:
            # 1. Search papers
            papers_result = await self.search_papers(query, k=k, paper_ids=paper_ids)
            if papers_result.status == "success":
                all_results.extend([
                    {**r, "source": "papers", "source_rank": i+1}
                    for i, r in enumerate(papers_result.results)
                ])
                metadata["sources"]["papers"] = papers_result.metadata["count"]
            
            # 2. Search chunks
            chunks_result = await self.search_chunks(query, k=k, paper_ids=paper_ids)
            if chunks_result.status == "success":
                all_results.extend([
                    {**r, "source": "chunks", "source_rank": i+1}
                    for i, r in enumerate(chunks_result.results)
                ])
                metadata["sources"]["chunks"] = chunks_result.metadata["count"]
            
            # 3. Search history (if enabled)
            if include_history:
                history_result = await self.search_history(chat_id, query=query, limit=10)
                if history_result.status == "success":
                    all_results.extend([
                        {**r, "source": "history", "source_rank": i+1}
                        for i, r in enumerate(history_result.results)
                    ])
                    metadata["sources"]["history"] = history_result.metadata["count"]
            
            return ToolResult(
                tool_name="combined_search",
                status="success",
                message=f"Combined search: {len(all_results)} results from multiple sources",
                results=all_results,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"[combined_search] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="combined_search",
                status="error",
                message=f"Failed to perform combined search: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    async def analyze_focus_papers(
        self,
        chat_id: str,
    ) -> ToolResult:
        """Analyze papers currently in focus scope.
        
        Use this when:
        - Want to understand what papers are in current scope
        - Need to see details of focused papers
        - Checking what's available in current research context
        
        Args:
            chat_id: Conversation ID
            
        Returns:
            ToolResult with focused papers details
        """
        logger.info(f"[analyze_focus_papers] Chat: {chat_id}")
        
        try:
            async with get_session() as session:
                async with session.begin():
                    # Get ChatThread to fetch focused_paper_ids
                    thread = (
                        await session.execute(
                            select(ChatThread).where(ChatThread.chat_id == chat_id)
                        )
                    ).scalar_one_or_none()
                    
                    if not thread or not thread.focused_paper_ids:
                        return ToolResult(
                            tool_name="analyze_focus_papers",
                            status="success",
                            message="No focused papers in current scope",
                            results=[],
                            metadata={
                                "chat_id": chat_id,
                                "focused_count": 0,
                            },
                        )
                    
                    # Get details of focused papers
                    stmt = (
                        select(
                            RPAbstractData.id,
                            RPAbstractData.title,
                            RPAbstractData.authors,
                            RPAbstractData.published,
                            RPAbstractData.link,
                            RPAbstractData.primary_category,
                            func.count(PaperAbstractChunk.id).label("chunk_count"),
                            func.count(Chunk.id).label("body_chunk_count"),
                        )
                        .outerjoin(PaperAbstractChunk, PaperAbstractChunk.rp_abstract_id == RPAbstractData.id)
                        .outerjoin(Chunk, Chunk.rp_abstract_id == RPAbstractData.id)
                        .where(RPAbstractData.id.in_(thread.focused_paper_ids))
                        .group_by(
                            RPAbstractData.id,
                            RPAbstractData.title,
                            RPAbstractData.authors,
                            RPAbstractData.published,
                            RPAbstractData.link,
                            RPAbstractData.primary_category,
                        )
                    )
                    
                    rows = (await session.execute(stmt)).mappings().all()
            
            result_dicts = [
                {
                    "paper_id": str(r["id"]),
                    "title": r["title"],
                    "authors": r["authors"],
                    "published": r["published"].isoformat() if r["published"] else None,
                    "link": r["link"],
                    "category": r["primary_category"],
                    "chunk_count": int(r["chunk_count"]),
                    "body_chunk_count": int(r["body_chunk_count"]),
                }
                for r in rows
            ]
            
            return ToolResult(
                tool_name="analyze_focus_papers",
                status="success",
                message=f"Found {len(result_dicts)} papers in focus scope",
                results=result_dicts,
                metadata={
                    "chat_id": chat_id,
                    "focused_count": len(result_dicts),
                    "total_abstract_chunks": sum(r["chunk_count"] for r in result_dicts),
                    "total_body_chunks": sum(r["body_chunk_count"] for r in result_dicts),
                },
            )
        except Exception as e:
            logger.error(f"[analyze_focus_papers] ERROR: {e}", exc_info=True)
            return ToolResult(
                tool_name="analyze_focus_papers",
                status="error",
                message=f"Failed to analyze focused papers: {str(e)}",
                results=[],
                metadata={"error": str(e)},
            )

    @staticmethod
    def _result_to_dict(result: SearchResult, include_full_text: bool = False) -> dict[str, Any]:
        """Convert SearchResult dataclass to dict for JSON serialization."""
        d = {
            "id": result.id,
            "source_type": result.source_type,
            "title": result.title,
            "text": result.text,
            "authors": result.authors,
            "link": result.link,
            "similarity_score": round(result.similarity_score, 4),
            "fts_rank": round(result.fts_rank, 4) if result.fts_rank else None,
            "combined_score": round(result.combined_score, 4),
        }
        
        if include_full_text and result.metadata.get("full_text"):
            d["full_text"] = result.metadata["full_text"]
        
        if result.metadata.get("rp_abstract_id"):
            d["paper_id"] = result.metadata["rp_abstract_id"]
        
        if result.metadata.get("chunk_id"):
            d["chunk_id"] = result.metadata["chunk_id"]

        if result.metadata.get("source_table"):
            d["source_table"] = result.metadata["source_table"]

        if result.metadata.get("section"):
            d["section"] = result.metadata["section"]

        if result.metadata.get("page_start") is not None:
            d["page_start"] = result.metadata["page_start"]

        if result.metadata.get("page_end") is not None:
            d["page_end"] = result.metadata["page_end"]

        if result.metadata.get("doc_id"):
            d["doc_id"] = result.metadata["doc_id"]
        
        return d


def create_langchain_tools(tools_instance: ResearchTools):
    """Create LangChain tool wrappers for the research tools.
    
    Returns a list of LangChain tool decorators ready for agent use.
    """
    
    @tool(name="search_papers")
    async def tool_search_papers(
        query: str,
        k: int = 10,
        paper_ids: Optional[str] = None,
    ) -> str:
        """Search paper summaries for research discovery and overview.
        
        Use when:
        - User asks "find papers on X", "what research exists on Y"
        - Want high-level paper overview before diving into details
        - Looking for papers by topic, author, or category
        - Need paper metadata (title, authors, categories, links)
        
        Args:
            query: Your research question or topic (e.g., "attention mechanisms")
            k: Number of papers to return (1-20, default 10)
            paper_ids: Comma-separated UUIDs to filter search (optional)
        
        Returns:
            JSON with found papers including title, authors, link, relevance score
        """
        paper_ids_list = None
        if paper_ids:
            paper_ids_list = [p.strip() for p in paper_ids.split(",")]
        
        result = await tools_instance.search_papers(query, k=k, paper_ids=paper_ids_list)
        return json.dumps(result.__dict__, default=str)

    @tool(name="search_chunks")
    async def tool_search_chunks(
        query: str,
        k: int = 10,
        paper_ids: Optional[str] = None,
    ) -> str:
        """Search paper chunks for technical details and implementation answers.
        
        Use when:
        - User asks "how do they implement X?", "what's the technique for Y?"
        - Need specific technical details, methodology, or findings
        - Want direct quotes or precise explanations
        - Looking for implementation details, algorithms, or results
        
        Args:
            query: Your technical question (e.g., "how does attention work?")
            k: Number of chunks to return (1-20, default 10)
            paper_ids: Comma-separated UUIDs to filter (optional)
        
        Data Access:
        - paperAbstractChunk: text, llm_summary, embedding, fts
        - chunk_data: text, llm_summary, section, page_start, page_end, embedding, fts

        Returns:
            JSON with detailed chunks including source_table, full text, summary, relevance score
        """
        paper_ids_list = None
        if paper_ids:
            paper_ids_list = [p.strip() for p in paper_ids.split(",")]
        
        result = await tools_instance.search_chunks(query, k=k, paper_ids=paper_ids_list)
        return json.dumps(result.__dict__, default=str)

    @tool(name="search_both")
    async def tool_search_both(
        query: str,
        k: int = 10,
        paper_ids: Optional[str] = None,
    ) -> str:
        """Comprehensive dual-stage search: papers THEN chunks from those papers.
        
        Use when:
        - User asks open-ended research questions
        - Want comprehensive answer with overview + technical details
        - Need both "what papers exist" AND "what do they do"
        - Asking "comprehensive overview of X" or "latest trends in Y"
        
        Process:
        1. Find relevant papers
        2. Get detailed chunks from those papers
        3. Return both for complete picture
        
        Args:
            query: Your research question (e.g., "transformers in NLP overview")
            k: Results per search (1-20, default 10)
            paper_ids: Comma-separated UUIDs to filter (optional)
        
        Returns:
            JSON with both papers and chunks, ranked by relevance
        """
        paper_ids_list = None
        if paper_ids:
            paper_ids_list = [p.strip() for p in paper_ids.split(",")]
        
        result = await tools_instance.search_both(query, k=k, paper_ids=paper_ids_list)
        return json.dumps(result.__dict__, default=str)

    @tool(name="search_history")
    async def tool_search_history(
        chat_id: str,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Search conversation history for context and previous discussions.
        
        Use when:
        - Need to recall what was discussed earlier
        - Want to check if something was already answered
        - Need context from previous messages
        - Looking for follow-ups on earlier answers
        
        Args:
            chat_id: Current conversation ID
            query: Optional keyword to search within history (e.g., "attention")
            limit: Number of messages to retrieve (1-50, default 20)
        
        Returns:
            JSON with historical messages in chronological order
        """
        result = await tools_instance.search_history(chat_id, query=query, limit=limit)
        return json.dumps(result.__dict__, default=str)

    @tool(name="combined_search")
    async def tool_combined_search(
        query: str,
        chat_id: str,
        k: int = 10,
        paper_ids: Optional[str] = None,
        include_history: bool = True,
    ) -> str:
        """All-in-one search: papers + chunks + conversation history.
        
        Use when:
        - Want everything: papers, technical details, and conversation context
        - Building a comprehensive answer from all sources
        - Need full picture without multiple tool calls
        
        Returns:
        - Papers: High-level research overview
        - Chunks: Technical implementation details
        - History: Previous discussion context
        
        Args:
            query: Your research question
            chat_id: Current conversation ID
            k: Results per source (1-20, default 10)
            paper_ids: Comma-separated UUIDs to filter (optional)
            include_history: Whether to include past messages (default True)
        
        Returns:
            JSON with all results merged, tagged by source type
        """
        paper_ids_list = None
        if paper_ids:
            paper_ids_list = [p.strip() for p in paper_ids.split(",")]
        
        result = await tools_instance.combined_search(
            query, chat_id, k=k, paper_ids=paper_ids_list, include_history=include_history
        )
        return json.dumps(result.__dict__, default=str)

    @tool(name="analyze_focus_papers")
    async def tool_analyze_focus_papers(chat_id: str) -> str:
        """Analyze papers currently in research scope/focus.
        
        Use when:
        - Want to see what papers are in current focus
        - Need to understand available research in scope
        - Checking what's available before searching
        
        Args:
            chat_id: Current conversation ID
        
        Returns:
            JSON with focused papers and their metadata
        """
        result = await tools_instance.analyze_focus_papers(chat_id)
        return json.dumps(result.__dict__, default=str)

    return [
        tool_search_papers,
        tool_search_chunks,
        tool_search_both,
        tool_search_history,
        tool_combined_search,
        tool_analyze_focus_papers,
    ]
