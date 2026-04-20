"""Repository for agent-related DB operations: history, runs, events."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvent, AgentRun, ChatMessage, ChatThread

logger = logging.getLogger(__name__)


def _make_serialisable(obj: Any) -> Any:
    """Recursively convert non-JSON-serialisable objects (e.g. LangChain messages)."""
    if isinstance(obj, BaseMessage):
        return {"type": obj.__class__.__name__, "content": obj.content}
    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serialisable(i) for i in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _msg_from_row(role: str, content: str) -> BaseMessage:
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return SystemMessage(content=content)


def _validate_role(role: str) -> str:
    role = role.strip().lower()
    if role not in ("user", "assistant", "system", "tool"):
        raise ValueError(f"Invalid role: {role}")
    return role


# ── History ─────────────────────────────────────────────────────────────────


class HistoryStore:
    async def ensure_thread(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        chat_id: str,
        title: Optional[str] = None,
    ) -> None:
        exists_q = await session.execute(
            select(ChatThread.chat_id).where(ChatThread.chat_id == chat_id)
        )
        if not exists_q.scalar_one_or_none():
            session.add(ChatThread(chat_id=chat_id, user_id=user_id, title=title))
        else:
            await session.execute(
                update(ChatThread)
                .where(ChatThread.chat_id == chat_id)
                .values(updated_at=dt.datetime.now(dt.timezone.utc))
            )

    async def append(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
    ) -> None:
        role = _validate_role(role)
        session.add(ChatMessage(chat_id=chat_id, user_id=user_id, role=role, content=content))
        await session.execute(
            update(ChatThread)
            .where(ChatThread.chat_id == chat_id)
            .values(updated_at=dt.datetime.now(dt.timezone.utc))
        )

    async def load(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        limit: int,
    ) -> list[BaseMessage]:
        q = (
            select(ChatMessage.role, ChatMessage.content)
            .where(
                ChatMessage.chat_id == chat_id,
                ChatMessage.role.in_(("user", "assistant")),
            )
            .order_by(desc(ChatMessage.msg_id))
            .limit(limit)
        )
        rows = (await session.execute(q)).all()
        rows = list(reversed(rows))
        return [_msg_from_row(r[0], r[1]) for r in rows]


# ── Runs ────────────────────────────────────────────────────────────────────


class RunStore:
    async def start_run(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        chat_id: str,
        user_id: str,
        idempotency_key: Optional[str],
    ) -> str:
        if idempotency_key:
            existing = await session.execute(
                select(AgentRun.run_id)
                .where(AgentRun.chat_id == chat_id, AgentRun.idempotency_key == idempotency_key)
            )
            existing_id = existing.scalar_one_or_none()
            if existing_id:
                return existing_id

        session.add(AgentRun(
            run_id=run_id,
            chat_id=chat_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            status="started",
            attempt_count=0,
        ))
        return run_id

    async def update_run(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        status: str,
        router_plan: Optional[dict] = None,
        attempt_count: Optional[int] = None,
        error: Optional[dict] = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": dt.datetime.now(dt.timezone.utc),
        }
        if router_plan is not None:
            values["router_plan"] = router_plan
        if attempt_count is not None:
            values["attempt_count"] = attempt_count
        if error is not None:
            values["error"] = error
        await session.execute(update(AgentRun).where(AgentRun.run_id == run_id).values(**values))


# ── Events ──────────────────────────────────────────────────────────────────


class EventStore:
    async def append_event(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        seq: int,
        event_type: str,
        payload: dict,
    ) -> None:
        safe_payload = _make_serialisable(payload)
        session.add(AgentEvent(run_id=run_id, seq=seq, event_type=event_type, payload=safe_payload))

    async def load_events(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[dict]:
        q = (
            select(AgentEvent.seq, AgentEvent.event_type, AgentEvent.payload, AgentEvent.created_at)
            .where(AgentEvent.run_id == run_id, AgentEvent.seq > after_seq)
            .order_by(AgentEvent.seq.asc())
            .limit(limit)
        )
        rows = (await session.execute(q)).all()
        return [{"seq": r[0], "event_type": r[1], "payload": r[2], "created_at": r[3]} for r in rows]
