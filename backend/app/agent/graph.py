"""
LangGraph research agent with durable execution, RAG, streaming, and retry.

Architecture:
    ingest → fetch_history → refresh_summary → rewrite_query → need_context? → (plan_sources → rag?)
    → compose → write → review → persist
  Review gate loops back on failure up to max_retries.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langchain_groq import ChatGroq
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, update

from app.agent.vector_store import PgVectorStore, SearchTarget
from app.db.engine import get_session
from app.db.models import ChatMessage, ChatThread
from app.db.repositories.agents import EventStore, HistoryStore, RunStore
from app.settings import get_settings
from app.prompts import PromptLoader

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentConfig:
    max_retries: int = 3
    enable_review: bool = True
    rewrite_history_k: int = 10
    compose_history_k: int = 10
    summary_every_n_messages: int = 15
    summary_max_chars: int = 3000
    rag_expand_n: int = 3
    rag_per_query_k: int = 10
    rag_fused_k: int = 8
    rag_final_k: int = 5
    rrf_k: int = 60


# ── State ───────────────────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    user_id: str
    chat_id: str
    run_id: str
    query: str
    rewritten_query: str
    attempt: int
    need_context: bool
    use_history: bool
    use_rag: bool
    history_msgs: List[BaseMessage]
    rolling_summary: str
    rolling_summary_msg_count: int
    expanded_queries: List[str]
    search_target: SearchTarget  # Which table(s) to search (papers, chunks, or both)
    rag_results_by_query: List[List[Dict[str, Any]]]
    rag_chunks: List[Dict[str, Any]]
    prompt_msgs: List[BaseMessage]
    draft: str
    review: Dict[str, Any]
    focused_paper_ids: List[str]
    thinking_mode: Literal["fast", "detailed"]
    llm_provider: Literal["ollama", "groq"]


# ── RRF fusion ──────────────────────────────────────────────────────────────


def _rrf_fuse(
    results_by_query: List[List[Dict[str, Any]]], *, fused_k: int, rrf_k: int
) -> List[Dict[str, Any]]:
    scores: Dict[str, float] = {}
    payload: Dict[str, Dict[str, Any]] = {}
    for lst in results_by_query:
        for rank, item in enumerate(lst, 1):
            doc_id = str(item.get("id") or item.get("text", "")[:80])
            payload[doc_id] = item
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [payload[did] for did, _ in ranked[:fused_k]]


def _parse_llm_json(content: str) -> Dict[str, Any]:
    raw = (content or "").strip()
    if not raw:
        raise json.JSONDecodeError("Empty response", raw, 0)

    # Direct JSON first.
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        raise json.JSONDecodeError("Top-level JSON is not an object", raw, 0)
    except json.JSONDecodeError:
        pass

    # Handle fenced markdown blocks like ```json ... ```.
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    if fence:
        return json.loads(fence.group(1))

    # Handle extra prose with embedded JSON object.
    obj = re.search(r"(\{[\s\S]*\})", raw)
    if obj:
        return json.loads(obj.group(1))

    raise json.JSONDecodeError("No JSON object found in response", raw, 0)


def _normalize_review_reasons(value: Any) -> str:
    """Normalize reviewer reasons to a human-readable string."""
    if isinstance(value, str):
        return value.strip().strip('"').strip()
    if isinstance(value, list):
        parts = [str(v).strip().strip('"') for v in value if str(v).strip()]
        return "\n".join(parts)
    return ""


def _fallback_parse_review(content: str) -> Dict[str, Any]:
    """Best-effort parser for malformed reviewer outputs.

    Handles common broken JSON patterns from LLMs, such as:
    - extra quote before closing list bracket
    - reasons returned as a list instead of a string
    """
    raw = (content or "").strip()
    approved = False
    reasons = ""

    m_approved = re.search(r'"approved"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
    if m_approved:
        approved = m_approved.group(1).lower() == "true"

    # First try a plain string reasons field.
    m_reason_str = re.search(r'"reasons"\s*:\s*"([\s\S]*?)"\s*(?:,|\})', raw)
    if m_reason_str:
        reasons = m_reason_str.group(1).encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        return {"approved": approved, "reasons": _normalize_review_reasons(reasons)}

    # Then handle list-style reasons field (possibly malformed).
    m_reason_arr = re.search(r'"reasons"\s*:\s*\[([\s\S]*?)\]\s*(?:,|\})?', raw)
    if m_reason_arr:
        inner = m_reason_arr.group(1)
        items = re.findall(r'"([\s\S]*?)"', inner)
        if items:
            decoded = [it.encode("utf-8", "ignore").decode("unicode_escape", "ignore") for it in items]
            return {"approved": approved, "reasons": _normalize_review_reasons(decoded)}

    return {"approved": approved, "reasons": "Reviewer output was malformed."}


# ── Agent ───────────────────────────────────────────────────────────────────


class ResearchAgent:
    def __init__(
        self,
        *,
        config: AgentConfig | None = None,
        history_store: HistoryStore | None = None,
        run_store: RunStore | None = None,
        event_store: EventStore | None = None,
        vector_store: PgVectorStore | None = None,
    ):
        s = get_settings()
        self.cfg = config or AgentConfig(
            max_retries=s.agent_max_retries,
            enable_review=s.agent_enable_review,
            rewrite_history_k=s.agent_rewrite_history_k,
            compose_history_k=s.agent_compose_history_k,
            summary_every_n_messages=s.agent_summary_every_n_messages,
            summary_max_chars=s.agent_summary_max_chars,
            rag_expand_n=s.agent_rag_expand_n,
            rag_per_query_k=s.agent_rag_per_query_k,
            rag_fused_k=s.agent_rag_fused_k,
            rag_final_k=s.agent_rag_final_k,
            rrf_k=s.agent_rrf_k,
        )
        self.history = history_store or HistoryStore()
        self.runs = run_store or RunStore()
        self.events = event_store or EventStore()
        self.vs = vector_store or PgVectorStore()
        self._llm_timeout_seconds = max(10, int(s.agent_llm_timeout_seconds))

        # Build one LLM bundle per available provider.
        # A bundle is a dict keyed by role: "router", "writer", "summary", "review".
        def _make_ollama_bundle() -> dict:
            return {
                "router": ChatOllama(model=s.llm_model_name, base_url=s.model_base_url, temperature=0),
                "writer": ChatOllama(model=s.llm_model_name, base_url=s.model_base_url, temperature=0.3),
                "summary": ChatOllama(model=s.llm_model_name, base_url=s.model_base_url, temperature=0.1),
                "review": (
                    ChatOllama(model=s.llm_model_name, base_url=s.model_base_url, temperature=0)
                    if self.cfg.enable_review else None
                ),
            }

        def _make_groq_bundle() -> dict:
            return {
                "router": ChatGroq(model=s.groq_llm_model_name, groq_api_key =s.groq_api_key, temperature=0),
                "writer": ChatGroq(model=s.groq_llm_model_name, groq_api_key =s.groq_api_key, temperature=0.3),
                "summary": ChatGroq(model=s.groq_llm_model_name, groq_api_key =s.groq_api_key, temperature=0.1),
                "review": (
                    ChatGroq(model=s.groq_llm_model_name, groq_api_key =s.groq_api_key, temperature=0)
                    if self.cfg.enable_review else None
                ),
            }

        self._llm_bundles: dict[str, dict] = {
            "ollama": _make_ollama_bundle(),
        }
        if s.groq_api_key:
            self._llm_bundles["groq"] = _make_groq_bundle()

        # Backward-compat attributes pointing to the env-configured default.
        _default = self._llm_bundles.get(s.llm_provider.lower(), self._llm_bundles["ollama"])
        self._llm_router = _default["router"]
        self._llm_writer = _default["writer"]
        self._llm_summary = _default["summary"]
        self._llm_review = _default["review"]

        self._checkpointer: Optional[AsyncPostgresSaver] = None
        self._checkpointer_cm = None
        self._app = None

    def _llm(self, s: AgentState, role: str):
        """Return the LLM client for *role* based on the per-request llm_provider in state."""
        provider = (s.get("llm_provider") or "ollama").lower()
        bundle = self._llm_bundles.get(provider)
        if bundle is None:
            available = ", ".join(sorted(self._llm_bundles.keys()))
            raise RuntimeError(
                f"LLM provider '{provider}' is not configured in the API container. "
                f"Available providers: {available or 'none'}. "
                "Set GROQ_API_KEY and restart the API service to enable Groq."
            )
        return bundle[role]

    async def _focused_paper_ids_for_thread(self, chat_id: str) -> List[str]:
        async with get_session() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ChatThread.focused_paper_ids, ChatThread.title).where(ChatThread.chat_id == chat_id)
                    )
                ).one_or_none()
        if not row:
            return []
        focused_ids = [str(x) for x in (row.focused_paper_ids or []) if x]
        if focused_ids:
            return focused_ids
        title = row.title
        if not title:
            return []
        m = re.search(r"\[pid:([0-9a-fA-F\-]{36})\]", title)
        return [m.group(1)] if m else []

    async def startup(self) -> None:
        s = get_settings()
        logger.info(f"[Agent.startup] Initializing agent...")
        logger.info(f"[Agent.startup]   LLM Base URL: {s.model_base_url}")
        logger.info(f"[Agent.startup]   LLM Model: {s.llm_model_name}")
        logger.info(f"[Agent.startup]   Default LLM Provider: {s.llm_provider}")
        logger.info(f"[Agent.startup]   Available Providers: {sorted(self._llm_bundles.keys())}")
        logger.info(f"[Agent.startup]   Embedder Model: {s.embedder_model_name}")
        logger.info(
            f"[Agent.startup]   Config: enable_review={self.cfg.enable_review}"
        )

        try:
            self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(s.psycopg_database_url)
            self._checkpointer = await self._checkpointer_cm.__aenter__()
            await self._checkpointer.setup()
            logger.info("[Agent.startup] ✓ Checkpointer initialized")
        except Exception as e:
            logger.error(f"[Agent.startup] ✗ Checkpointer setup FAILED: {e}", exc_info=True)
            raise

        try:
            self._app = self._build_graph().compile(checkpointer=self._checkpointer)
            logger.info("[Agent.startup] ✓ Graph compiled")
        except Exception as e:
            logger.error(f"[Agent.startup] ✗ Graph compilation FAILED: {e}", exc_info=True)
            raise

        logger.info("[Agent.startup] ✓ Research agent fully started")

    async def shutdown(self) -> None:
        if self._checkpointer_cm is not None:
            await self._checkpointer_cm.__aexit__(None, None, None)
            self._checkpointer_cm = None
            self._checkpointer = None

    def _format_history(self, msgs: List[BaseMessage], *, limit: Optional[int] = None) -> str:
        selected = msgs[-limit:] if limit and limit > 0 else msgs
        lines: List[str] = []
        for i, m in enumerate(selected, 1):
            role = "assistant" if isinstance(m, AIMessage) else "user"
            lines.append(f"{i}. {role}: {str(m.content).strip()}")
        return "\n".join(lines) if lines else "(no prior messages)"

    def _writer_system_prompt(self) -> str:
        return PromptLoader.load("writer_system")

    def _router_need_context_prompt(self) -> str:
        return PromptLoader.load("router_need_context")

    def _router_plan_prompt(self) -> str:
        return PromptLoader.load("router_plan")

    def _rewrite_prompt(self) -> str:
        return PromptLoader.load("rewrite_query")

    def _rag_expand_prompt(self, n: int) -> str:
        template = PromptLoader.load("rag_expand")
        return template.format(n_queries=n)

    def _review_prompt(self) -> str:
        return PromptLoader.load("review")

    def _summary_prompt(self) -> str:
        return PromptLoader.load("summary")

    def _rag_routing_prompt(self) -> str:
        """Route query to appropriate search target (papers, chunks, or both)."""
        return PromptLoader.load("rag_routing")

    # ── Nodes ───────────────────────────────────────────────────────────

    async def _ingest(self, s: AgentState) -> AgentState:
        q = s["query"].strip()
        return {
            **s,
            "query": q,
            "rewritten_query": q,
            "search_target": SearchTarget.CHUNKS,  # Default to searching chunks for details
            "attempt": int(s.get("attempt", 0)),
        }

    async def _fetch_history(self, s: AgentState) -> AgentState:
        history_limit = max(self.cfg.rewrite_history_k, self.cfg.compose_history_k)
        async with get_session() as session:
            async with session.begin():
                msgs = await self.history.load(session, chat_id=s["chat_id"], limit=history_limit)
                thread_row = (
                    await session.execute(
                        select(ChatThread.rolling_summary, ChatThread.rolling_summary_msg_count).where(
                            ChatThread.chat_id == s["chat_id"]
                        )
                    )
                ).one_or_none()
        rolling_summary = (thread_row.rolling_summary or "") if thread_row else ""
        covered_count = int(thread_row.rolling_summary_msg_count or 0) if thread_row else 0
        return {
            **s,
            "history_msgs": msgs,
            "rolling_summary": rolling_summary,
            "rolling_summary_msg_count": covered_count,
        }

    async def _rewrite_query(self, s: AgentState) -> AgentState:
        history_text = self._format_history(s.get("history_msgs", []), limit=self.cfg.rewrite_history_k)
        prompt = [
            SystemMessage(content=self._rewrite_prompt()),
            HumanMessage(
                content=(
                    f"Original query:\n{s['query']}\n\n"
                    f"Recent conversation:\n{history_text}\n\n"
                    "Rewrite now."
                )
            ),
        ]
        try:
            msg = await self._llm(s, "router").ainvoke(prompt)
            data = _parse_llm_json(msg.content)
            rewritten = str(data.get("rewritten_query", "")).strip() or s["query"]
            logger.info(f"[_rewrite_query] ✓ Rewritten query: {rewritten[:120]}")
        except Exception as e:
            logger.error(f"[_rewrite_query] Rewrite failed: {type(e).__name__}: {e}", exc_info=True)
            rewritten = s["query"]
        return {**s, "rewritten_query": rewritten}

    async def _need_context(self, s: AgentState) -> AgentState:
        q = s.get("rewritten_query", s["query"]).strip()
        q_l = q.lower()
        tokens = re.findall(r"[a-z0-9']+", q_l)
        smalltalk_phrases = {
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "ok",
            "okay",
            "good morning",
            "good evening",
        }
        smalltalk_tokens = {"hi", "hello", "hey", "thanks", "thank", "ok", "okay"}
        research_markers = {
            "paper",
            "papers",
            "research",
            "arxiv",
            "summary",
            "abstract",
            "method",
            "results",
            "findings",
            "compare",
            "citation",
            "model",
            "dataset",
        }

        heuristic_need = True
        if s.get("focused_paper_ids"):
            heuristic_need = True
        elif q_l in smalltalk_phrases or (len(tokens) <= 3 and any(tok in smalltalk_tokens for tok in tokens)):
            heuristic_need = False
        elif q.endswith("?") or any(tok in research_markers for tok in tokens):
            heuristic_need = True

        logger.debug("[_need_context] Using LLM routing...")
        history_text = self._format_history(s.get("history_msgs", []), limit=self.cfg.rewrite_history_k)
        prompt = [
            SystemMessage(content=self._router_need_context_prompt()),
            HumanMessage(content=(
                f"Original query:\n{s['query']}\n\n"
                f"Rewritten query:\n{s.get('rewritten_query', s['query'])}\n\n"
                f"Focused paper IDs:\n{s.get('focused_paper_ids', [])}\n\n"
                f"Recent history:\n{history_text}"
            )),
        ]
        try:
            msg = await self._llm(s, "router").ainvoke(prompt)
            logger.debug(f"[_need_context] LLM response: {msg.content[:100]}")
            data = _parse_llm_json(msg.content)
            need = bool(data.get("need_context", False))
            logger.info(f"[_need_context] ✓ Routing decision: need={need}")
        except json.JSONDecodeError as e:
            logger.error(f"[_need_context] JSON parse FAILED! Response: {msg.content!r} | Error: {e}")
            need = heuristic_need
        except Exception as e:
            logger.error(f"[_need_context] ERROR: {type(e).__name__}: {e}", exc_info=True)
            need = heuristic_need
        return {**s, "need_context": need}

    async def _plan_sources(self, s: AgentState) -> AgentState:
        rewritten_query = s.get("rewritten_query", s["query"])
        query_l = rewritten_query.lower()
        need_context = bool(s.get("need_context", True))

        history_markers = (
            "previous",
            "earlier",
            "last answer",
            "you said",
            "that paper",
            "this paper",
            "continue",
            "follow up",
            "as above",
            "based on your answer",
        )
        use_h_heuristic = any(marker in query_l for marker in history_markers)
        use_r_heuristic = need_context
        if s.get("focused_paper_ids"):
            use_r_heuristic = True

        logger.debug("[_plan_sources] Using LLM routing...")
        history_text = self._format_history(s.get("history_msgs", []), limit=self.cfg.rewrite_history_k)
        prompt = [
            SystemMessage(content=self._router_plan_prompt()),
            HumanMessage(content=(
                f"Original query:\n{s['query']}\n\n"
                f"Rewritten query:\n{rewritten_query}\n\n"
                f"Focused paper IDs:\n{s.get('focused_paper_ids', [])}\n\n"
                f"Recent history:\n{history_text}"
            )),
        ]
        try:
            msg = await self._llm(s, "router").ainvoke(prompt)
            logger.debug(f"[_plan_sources] LLM response: {msg.content[:100]}")
            data = _parse_llm_json(msg.content)
            use_h, use_r = bool(data.get("use_history")), bool(data.get("use_rag"))
            if need_context and not (use_h or use_r):
                use_r = True
            if s.get("focused_paper_ids") and not use_r:
                use_r = True
            logger.info(f"[_plan_sources] ✓ Plan: history={use_h}, rag={use_r}")
        except json.JSONDecodeError as e:
            logger.error(f"[_plan_sources] JSON parse FAILED! Response: {msg.content!r} | Error: {e}")
            use_h, use_r = use_h_heuristic, use_r_heuristic
        except Exception as e:
            logger.error(f"[_plan_sources] ERROR: {type(e).__name__}: {e}", exc_info=True)
            use_h, use_r = use_h_heuristic, use_r_heuristic

        async with get_session() as session:
            async with session.begin():
                await self.runs.update_run(
                    session, run_id=s["run_id"], status="streaming",
                    router_plan={"need_context": bool(s.get("need_context")), "use_history": use_h, "use_rag": use_r},
                )
        return {**s, "use_history": use_h, "use_rag": use_r}

    async def _rag_route(self, s: AgentState) -> AgentState:
        """Optional: Route to appropriate search target (papers, chunks, or both).
        
        This node uses an LLM to intelligently decide whether to search:
        - papers: For paper discovery queries (high-level overviews)
        - chunks: For technical detail queries (methodology, specifics)
        - both: For comprehensive research queries (dual-stage)
        """
        rewritten_query = s.get("rewritten_query", s["query"])
        focused_ids = s.get("focused_paper_ids") or []
        logger.info(f"[_rag_route] Routing query: {rewritten_query[:80]}")
        
        prompt = [
            SystemMessage(content=self._rag_routing_prompt()),
            HumanMessage(content=(
                f"User query: {rewritten_query}\n\n"
                f"Focused paper IDs: {focused_ids if focused_ids else '(none)'}\n\n"
                "Select target according to query intent and focus scope rules."
            )),
        ]
        
        try:
            msg = await self._llm(s, "router").ainvoke(prompt)
            data = _parse_llm_json(msg.content)
            target_str = str(data.get("target", "both")).lower().strip()
            
            # Validate and map to SearchTarget
            if target_str == "papers":
                target = SearchTarget.PAPERS
            elif target_str == "chunks":
                target = SearchTarget.CHUNKS
            else:
                target = SearchTarget.BOTH

            # If user is focused on specific papers, avoid overly broad paper-only routing
            # unless it is clearly a summary/metadata request.
            if focused_ids and target == SearchTarget.PAPERS:
                q_l = rewritten_query.lower()
                metadata_markers = (
                    "title", "author", "authors", "category", "published", "link", "metadata", "summary"
                )
                if not any(tok in q_l for tok in metadata_markers):
                    target = SearchTarget.BOTH
            
            logger.info(f"[_rag_route] ✓ Routed to target: {target.value}")
        except Exception as e:
            logger.error(f"[_rag_route] Routing FAILED, defaulting to BOTH: {e}")
            target = SearchTarget.BOTH
        
        return {**s, "search_target": target}

    async def _rag_expand(self, s: AgentState) -> AgentState:
        rewritten_query = s.get("rewritten_query", s["query"])
        logger.info(f"[_rag_expand] Expanding query: {rewritten_query[:80]}")
        history_text = self._format_history(s.get("history_msgs", []), limit=self.cfg.rewrite_history_k)
        prompt = [
            SystemMessage(content=self._rag_expand_prompt(self.cfg.rag_expand_n)),
            HumanMessage(content=(
                f"Original query:\n{s['query']}\n\n"
                f"Rewritten query:\n{rewritten_query}\n\n"
                f"Recent history:\n{history_text}"
            )),
        ]
        try:
            msg = await self._llm(s, "router").ainvoke(prompt)
            logger.debug(f"[_rag_expand] LLM response: {msg.content[:150]}")
            data = _parse_llm_json(msg.content)
            qs = [str(x).strip() for x in data.get("queries", []) if str(x).strip()]
            logger.info(f"[_rag_expand] ✓ Generated {len(qs)} queries")
        except json.JSONDecodeError as e:
            logger.error(f"[_rag_expand] JSON parse FAILED! Response: {msg.content!r} | Error: {e}")
            qs = []
        except Exception as e:
            logger.error(f"[_rag_expand] ERROR: {type(e).__name__}: {e}", exc_info=True)
            qs = []
        while len(qs) < self.cfg.rag_expand_n:
            qs.append(rewritten_query)
        logger.debug(f"[_rag_expand] Final queries: {qs}")
        return {**s, "expanded_queries": qs[:self.cfg.rag_expand_n]}

    async def _rag_retrieve(self, s: AgentState) -> AgentState:
        """Retrieve using hybrid search (vector + keyword) with RRF fusion."""
        out: List[List[Dict[str, Any]]] = []
        focused_ids = s.get("focused_paper_ids")
        search_target = s.get("search_target", SearchTarget.CHUNKS)
        
        logger.info(
            f"[_rag_retrieve] Hybrid search ({len(s['expanded_queries'])} queries, "
            f"target={search_target.value}) with paper filter: {len(focused_ids) if focused_ids else 'none'}"
        )
        
        async with get_session() as session:
            async with session.begin():
                for i, q in enumerate(s["expanded_queries"], 1):
                    # Multi-stage hybrid search: vector + keyword, then RRF fusion
                    results = await self.vs.search(
                        session,
                        query=q,
                        k=self.cfg.rag_per_query_k,
                        target=search_target,  # Use routed search target
                        paper_ids=focused_ids,
                        use_hybrid=True,  # Vector + keyword hybrid
                    )
                    
                    # Convert SearchResult dataclass to dict for compatibility with _rrf_fuse
                    result_dicts = [
                        {
                            "id": r.id,
                            "text": r.text,
                            "title": r.title,
                            "link": r.link,
                            "authors": r.authors,
                            "source_type": r.source_type,
                            "similarity_score": r.similarity_score,
                            "fts_rank": r.fts_rank,
                            "combined_score": r.combined_score,
                            **r.metadata,
                        }
                        for r in results
                    ]
                    
                    logger.debug(
                        f"[_rag_retrieve] Query {i}: '{q[:60]}...' returned "
                        f"{len(results)} results (vector + keyword hybrid)"
                    )
                    out.append(result_dicts)
        
        total_results = sum(len(r) for r in out)
        logger.info(f"[_rag_retrieve] ✓ Total {total_results} results from hybrid search across {len(out)} queries")
        return {**s, "rag_results_by_query": out}

    async def _rag_fuse(self, s: AgentState) -> AgentState:
        fused = _rrf_fuse(s["rag_results_by_query"], fused_k=self.cfg.rag_fused_k, rrf_k=self.cfg.rrf_k)
        return {**s, "rag_chunks": fused[:self.cfg.rag_final_k]}

    async def _refresh_summary(self, s: AgentState) -> AgentState:
        threshold = max(1, int(self.cfg.summary_every_n_messages))
        summary = (s.get("rolling_summary") or "").strip()
        covered = int(s.get("rolling_summary_msg_count", 0))

        async with get_session() as session:
            async with session.begin():
                total_msgs = int(
                    (
                        await session.execute(
                            select(func.count(ChatMessage.msg_id)).where(
                                ChatMessage.chat_id == s["chat_id"],
                                ChatMessage.role.in_(("user", "assistant")),
                            )
                        )
                    ).scalar_one()
                    or 0
                )

                updated = False
                while total_msgs - covered >= threshold:
                    rows = (
                        await session.execute(
                            select(ChatMessage.role, ChatMessage.content)
                            .where(
                                ChatMessage.chat_id == s["chat_id"],
                                ChatMessage.role.in_(("user", "assistant")),
                            )
                            .order_by(ChatMessage.msg_id.asc())
                            .offset(covered)
                            .limit(threshold)
                        )
                    ).all()
                    if not rows:
                        break

                    transcript = "\n".join(
                        f"{i}. {role}: {str(content).strip()}" for i, (role, content) in enumerate(rows, 1)
                    )
                    prompt = [
                        SystemMessage(content=self._summary_prompt()),
                        HumanMessage(content=(
                            f"Current query:\n{s['query']}\n\n"
                            f"Current rewritten query:\n{s.get('rewritten_query', s['query'])}\n\n"
                            f"Previous rolling summary:\n{summary or '(none)'}\n\n"
                            f"New message window ({len(rows)} messages):\n{transcript}\n\n"
                            "Produce the updated rolling summary now."
                        )),
                    ]

                    try:
                        msg = await self._llm(s, "summary").ainvoke(prompt)
                        data = _parse_llm_json(msg.content)
                        summary_candidate = str(data.get("summary", "")).strip()
                        if summary_candidate:
                            summary = summary_candidate
                    except Exception as e:
                        logger.error(f"[_refresh_summary] Summary update failed: {type(e).__name__}: {e}", exc_info=True)
                        if transcript:
                            summary = (summary + "\n" + transcript).strip() if summary else transcript

                    if len(summary) > self.cfg.summary_max_chars:
                        summary = summary[-self.cfg.summary_max_chars :]

                    covered += len(rows)
                    updated = True

                if updated:
                    await session.execute(
                        update(ChatThread)
                        .where(ChatThread.chat_id == s["chat_id"])
                        .values(
                            rolling_summary=summary,
                            rolling_summary_msg_count=covered,
                            rolling_summary_updated_at=datetime.now(timezone.utc),
                        )
                    )

        return {**s, "rolling_summary": summary, "rolling_summary_msg_count": covered}

    async def _compose(self, s: AgentState) -> AgentState:
        msgs: List[BaseMessage] = [SystemMessage(content=self._writer_system_prompt())]
        if s.get("review") and not s["review"].get("approved", True):
            reasons = s["review"].get("reasons", "")
            if reasons:
                msgs.append(SystemMessage(content=f"Fix these reviewer issues:\n{reasons}"))
        if s.get("rolling_summary"):
            msgs.append(SystemMessage(content=f"Rolling conversation summary:\n{s['rolling_summary']}"))

        recent = s.get("history_msgs", [])[-self.cfg.compose_history_k :]
        if recent:
            msgs.append(SystemMessage(content=f"Recent conversation window:\n{self._format_history(recent)}"))

        if s.get("rag_chunks"):
            excerpts = "\n\n".join(f"[{i+1}] {d.get('text','')}" for i, d in enumerate(s["rag_chunks"]))
            msgs.append(SystemMessage(content=f"Knowledge base excerpts:\n{excerpts}"))
        msgs.append(SystemMessage(content=f"Rewritten query for retrieval/routing:\n{s.get('rewritten_query', s['query'])}"))
        msgs.append(HumanMessage(content=s["query"]))
        return {**s, "prompt_msgs": msgs}

    async def _write(self, s: AgentState) -> AgentState:
        logger.info(f"[_write] Generating answer with {len(s.get('rag_chunks', []))} context chunks, " +
                   f"{len(s.get('history_msgs', []))} history messages")
        try:
            msg = await asyncio.wait_for(
                self._llm(s, "writer").ainvoke(s["prompt_msgs"]),
                timeout=self._llm_timeout_seconds,
            )
            draft = msg.content
            if not draft or len(draft.strip()) < 3:
                logger.warning(f"[_write] Generated very short answer: {draft!r}")
            else:
                logger.info(f"[_write] ✓ Answer generated ({len(draft)} chars)")
            return {**s, "draft": draft}
        except asyncio.TimeoutError:
            logger.error("[_write] TIMEOUT after %ss waiting for writer LLM", self._llm_timeout_seconds)
            return {**s, "draft": "[Agent timeout: writer model took too long to respond]"}
        except Exception as e:
            logger.error(f"[_write] ANSWER GENERATION FAILED: {type(e).__name__}: {e}", exc_info=True)
            return {**s, "draft": f"[Agent error: {str(e)[:80]}]"}

    async def _review(self, s: AgentState) -> AgentState:
        # Fast mode explicitly bypasses reviewer for lower latency responses.
        if s.get("thinking_mode", "detailed") == "fast":
            return {**s, "review": {"approved": True, "reasons": ""}}

        if self._llm(s, "review") is None:
            return {**s, "review": {"approved": True, "reasons": ""}}
        logger.info("[_review] Running quality review...")
        prompt = [
            SystemMessage(content=self._review_prompt()),
            HumanMessage(content=(
                f"Original query:\n{s['query']}\n\n"
                f"Rewritten query:\n{s.get('rewritten_query', s['query'])}\n\n"
                f"Rolling summary:\n{s.get('rolling_summary', '(none)')}\n\n"
                f"ANSWER:\n{s.get('draft','')}"
            )),
        ]
        try:
            msg = await asyncio.wait_for(
                self._llm(s, "review").ainvoke(prompt),
                timeout=self._llm_timeout_seconds,
            )
            logger.debug(f"[_review] LLM response: {msg.content[:100]}")
            data = _parse_llm_json(msg.content)
            review = {
                "approved": bool(data.get("approved")),
                "reasons": _normalize_review_reasons(data.get("reasons", "")),
            }
            logger.info(f"[_review] ✓ Review: approved={review['approved']}")
        except asyncio.TimeoutError:
            logger.error("[_review] TIMEOUT after %ss waiting for reviewer LLM", self._llm_timeout_seconds)
            review = {"approved": False, "reasons": "Reviewer timeout; retry."}
        except json.JSONDecodeError as e:
            logger.error(f"[_review] JSON parse FAILED! Response: {msg.content!r} | Error: {e}")
            review = _fallback_parse_review(msg.content if "msg" in locals() else "")
            logger.warning(
                "[_review] Applied fallback parser. approved=%s reasons=%s",
                review.get("approved"),
                (review.get("reasons") or "")[:140],
            )
        except Exception as e:
            logger.error(f"[_review] ERROR: {type(e).__name__}: {e}", exc_info=True)
            review = {"approved": False, "reasons": "Reviewer error; retry."}
        return {**s, "review": review}

    async def _persist(self, s: AgentState) -> AgentState:
        async with get_session() as session:
            async with session.begin():
                await self.history.append(session, chat_id=s["chat_id"], user_id=s["user_id"], role="user", content=s["query"])
                await self.history.append(session, chat_id=s["chat_id"], user_id=s["user_id"], role="assistant", content=s.get("draft", ""))
                await self.runs.update_run(session, run_id=s["run_id"], status="succeeded", attempt_count=int(s.get("attempt", 0)))
        return s

    async def _bump(self, s: AgentState) -> AgentState:
        attempt = int(s.get("attempt", 0)) + 1
        async with get_session() as session:
            async with session.begin():
                await self.runs.update_run(session, run_id=s["run_id"], status="streaming", attempt_count=attempt)
        return {**s, "attempt": attempt}

    # ── Edge routers ────────────────────────────────────────────────────

    def _route_need_context(self, s: AgentState) -> Literal["direct", "context"]:
        return "context" if s.get("need_context") else "direct"

    def _route_sources(self, s: AgentState) -> Literal["history", "rag", "both", "none"]:
        h, r = bool(s.get("use_history")), bool(s.get("use_rag"))
        if h and r:
            return "both"
        if h:
            return "history"
        if r:
            return "rag"
        return "none"

    def _route_retry(self, s: AgentState) -> Literal["retry", "send"]:
        approved = bool(s.get("review", {}).get("approved"))
        if approved or int(s.get("attempt", 0)) >= self.cfg.max_retries:
            return "send"
        return "retry"

    # ── Graph build ─────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        g = StateGraph(AgentState)
        for name, fn in [
            ("ingest", self._ingest), ("fetch_history", self._fetch_history),
            ("refresh_summary", self._refresh_summary),
            ("rewrite_query", self._rewrite_query), ("need_context", self._need_context),
            ("plan_sources", self._plan_sources),
            ("rag_route", self._rag_route),
            ("rag_expand", self._rag_expand), ("rag_retrieve", self._rag_retrieve),
            ("rag_fuse", self._rag_fuse),
            ("compose", self._compose),
            ("write", self._write), ("review", self._review),
            ("bump", self._bump), ("persist", self._persist),
        ]:
            g.add_node(name, fn)

        g.add_edge(START, "ingest")
        g.add_edge("ingest", "fetch_history")
        g.add_edge("fetch_history", "refresh_summary")
        g.add_edge("refresh_summary", "rewrite_query")
        g.add_edge("rewrite_query", "need_context")
        g.add_conditional_edges("need_context", self._route_need_context, {"direct": "compose", "context": "plan_sources"})
        g.add_conditional_edges("plan_sources", self._route_sources, {
            "history": "compose", "rag": "rag_route", "both": "rag_route", "none": "compose",
        })
        g.add_edge("rag_route", "rag_expand")
        g.add_edge("rag_expand", "rag_retrieve")
        g.add_edge("rag_retrieve", "rag_fuse")
        g.add_edge("rag_fuse", "compose")
        g.add_edge("compose", "write")
        if self.cfg.enable_review:
            g.add_edge("write", "review")
            g.add_conditional_edges("review", self._route_retry, {"retry": "bump", "send": "persist"})
            g.add_edge("bump", "fetch_history")
        else:
            g.add_edge("write", "persist")
        g.add_edge("persist", END)
        return g

    # ── Public API ──────────────────────────────────────────────────────

    async def run_stream(
        self,
        *,
        user_id: str,
        chat_id: str,
        query: str,
        idempotency_key: Optional[str] = None,
        focused_paper_ids: Optional[List[str]] = None,
        thinking_mode: Literal["fast", "detailed"] = "detailed",
        llm_provider: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        if not self._app:
            raise RuntimeError("Call startup() first")

        s = get_settings()
        requested_provider = (llm_provider or s.llm_provider or "ollama").lower()
        run_id = str(uuid.uuid4())
        logger.info(
            f"[run_stream] Starting: run_id={run_id}, chat_id={chat_id}, "
            f"llm_provider={requested_provider}, query={query[:60]}"
        )
        
        async with get_session() as session:
            async with session.begin():
                await self.runs.start_run(session, run_id=run_id, chat_id=chat_id, user_id=user_id, idempotency_key=idempotency_key)
                await self.runs.update_run(session, run_id=run_id, status="streaming")

        thread_paper_ids = await self._focused_paper_ids_for_thread(chat_id)
        chosen_paper_ids = [x for x in (focused_paper_ids or []) if x] or thread_paper_ids
        
        logger.info(f"[run_stream] Paper ID resolution: thread_stored={thread_paper_ids}, request_param={focused_paper_ids}, chosen={chosen_paper_ids}")
        if chosen_paper_ids:
            logger.info(f"[run_stream] ✓ Will filter RAG results to {len(chosen_paper_ids)} focused paper(s)")
        else:
            logger.info(f"[run_stream] No focused papers—will search entire knowledge base")
        
        state_in: AgentState = {
            "user_id": user_id,
            "chat_id": chat_id,
            "run_id": run_id,
            "query": query,
            "attempt": 0,
            "thinking_mode": thinking_mode,
            "llm_provider": requested_provider,
        }
        if chosen_paper_ids:
            state_in["focused_paper_ids"] = chosen_paper_ids
        config = {"configurable": {"thread_id": chat_id}}

        seq = 0
        try:
            async for update in self._app.astream(state_in, config=config, stream_mode="updates"):
                seq += 1
                payload = {"seq": seq, "update": update, "run_id": run_id}
                for node_name in update:
                    logger.debug(f"[run_stream] Node: {node_name}")
                yield payload

                # Persist events as best-effort bookkeeping. Never block SSE delivery
                # to the UI on event-store writes.
                try:
                    event_payload = {
                        "seq": seq,
                        "run_id": run_id,
                        "nodes": list(update.keys()),
                    }
                    async with get_session() as session:
                        async with session.begin():
                            await asyncio.wait_for(
                                self.events.append_event(
                                    session,
                                    run_id=run_id,
                                    seq=seq,
                                    event_type="state_update",
                                    payload=event_payload,
                                ),
                                timeout=2.0,
                            )
                except Exception as persist_exc:
                    logger.warning(
                        "[run_stream] Event persistence skipped at seq=%s: %s",
                        seq,
                        persist_exc,
                    )
            logger.info(f"[run_stream] ✓ Completed: run_id={run_id} ({seq} updates)")
        except Exception as exc:
            logger.error(f"[run_stream] ✗ FAILED: {type(exc).__name__}: {exc}", exc_info=True)
            async with get_session() as session:
                async with session.begin():
                    await self.runs.update_run(session, run_id=run_id, status="failed", error={"message": str(exc)})
            raise
