"""Thread & message management routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update

from app.api.deps import get_current_user
from app.api.schemas import (
    IndexedPaper,
    MessageResponse,
    ThreadCreate,
    ThreadResponse,
    ThreadScopeResponse,
    ThreadScopeUpdateRequest,
)
from app.db.engine import get_session
from app.db.models import ChatMessage, ChatThread, RPAbstractData
from app.db.repositories.agents import HistoryStore
from app.db.repositories.papers import get_indexed_papers_by_ids

router = APIRouter(prefix="/threads", tags=["threads"])
_history = HistoryStore()


def _to_thread_response(row: ChatThread) -> ThreadResponse:
    return ThreadResponse(
        chat_id=row.chat_id,
        title=row.title,
        focused_paper_ids=[str(x) for x in (row.focused_paper_ids or [])],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load_user_thread(chat_id: str, user_id: str) -> ChatThread:
    async with get_session() as db:
        async with db.begin():
            row = (
                await db.execute(
                    select(ChatThread).where(ChatThread.chat_id == chat_id, ChatThread.user_id == user_id)
                )
            ).scalar_one_or_none()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    return row


@router.get("", response_model=list[ThreadResponse])
async def list_threads(user_id: str = Depends(get_current_user)) -> list[ThreadResponse]:
    async with get_session() as db:
        async with db.begin():
            rows = (
                await db.execute(
                    select(ChatThread)
                    .where(ChatThread.user_id == user_id)
                    .order_by(ChatThread.updated_at.desc())
                )
            ).scalars().all()
    return [
        _to_thread_response(r)
        for r in rows
    ]


@router.post("", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
async def create_thread(req: ThreadCreate, user_id: str = Depends(get_current_user)) -> ThreadResponse:
    chat_id = str(uuid.uuid4())
    title = req.title

    paper = None
    focused_paper_ids: list[str] = []
    if req.paper_id:
        try:
            paper_uuid = uuid.UUID(req.paper_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid paper_id") from exc

        async with get_session() as db:
            async with db.begin():
                paper = (
                    await db.execute(
                        select(RPAbstractData.id, RPAbstractData.title)
                        .where(RPAbstractData.id == paper_uuid)
                    )
                ).first()
        if not paper:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
        focused_paper_ids = [str(paper[0])]
        if title:
            if f"[pid:{paper[0]}]" not in title:
                title = f"{title} [pid:{paper[0]}]"
        else:
            title = f"{paper[1]} [pid:{paper[0]}]"

    async with get_session() as db:
        async with db.begin():
            await _history.ensure_thread(db, user_id=user_id, chat_id=chat_id, title=title)
            await db.execute(
                update(ChatThread)
                .where(ChatThread.chat_id == chat_id)
                .values(focused_paper_ids=focused_paper_ids)
            )
            await db.flush()
    async with get_session() as db:
        async with db.begin():
            row = (await db.execute(select(ChatThread).where(ChatThread.chat_id == chat_id))).scalar_one()
    return _to_thread_response(row)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(chat_id: str, user_id: str = Depends(get_current_user)) -> None:
    async with get_session() as db:
        async with db.begin():
            row = (
                await db.execute(
                    select(ChatThread).where(ChatThread.chat_id == chat_id, ChatThread.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not row:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
            await db.delete(row)


@router.get("/{chat_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    chat_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str = Depends(get_current_user),
) -> list[MessageResponse]:
    async with get_session() as db:
        async with db.begin():
            thread = (
                await db.execute(
                    select(ChatThread.chat_id).where(ChatThread.chat_id == chat_id, ChatThread.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not thread:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

            rows = (
                await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.chat_id == chat_id,
                        ChatMessage.role.in_(("user", "assistant")),
                    )
                    .order_by(ChatMessage.msg_id.asc())
                    .limit(limit)
                )
            ).scalars().all()

    return [MessageResponse(msg_id=r.msg_id, role=r.role, content=r.content, created_at=r.created_at) for r in rows]


@router.get("/{chat_id}/scope", response_model=ThreadScopeResponse)
async def get_thread_scope(
    chat_id: str,
    user_id: str = Depends(get_current_user),
) -> ThreadScopeResponse:
    row = await _load_user_thread(chat_id, user_id)
    paper_ids = [str(x) for x in (row.focused_paper_ids or [])]

    async with get_session() as db:
        async with db.begin():
            papers = await get_indexed_papers_by_ids(db, paper_ids=paper_ids)

    return ThreadScopeResponse(
        paper_ids=paper_ids,
        papers=[
            IndexedPaper(
                paper_id=str(r["id"]),
                title=r["title"],
                summary=r.get("summary"),
                authors=r.get("authors"),
                categories=r.get("primary_category"),
                submitted_at=r.get("published"),
                link=r.get("link"),
                pdf_url=r.get("pdf_url"),
            )
            for r in papers
        ],
    )


@router.put("/{chat_id}/scope", response_model=ThreadScopeResponse)
async def update_thread_scope(
    chat_id: str,
    req: ThreadScopeUpdateRequest,
    user_id: str = Depends(get_current_user),
) -> ThreadScopeResponse:
    await _load_user_thread(chat_id, user_id)

    valid_ids: list[str] = []
    for pid in req.paper_ids:
        try:
            valid_ids.append(str(uuid.UUID(pid)))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid paper_id: {pid}") from exc

    async with get_session() as db:
        async with db.begin():
            papers = await get_indexed_papers_by_ids(db, paper_ids=valid_ids)
            found_ids = [str(r["id"]) for r in papers]
            missing = [pid for pid in valid_ids if pid not in found_ids]
            if missing:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Paper not found: {missing[0]}")

            await db.execute(
                update(ChatThread)
                .where(ChatThread.chat_id == chat_id, ChatThread.user_id == user_id)
                .values(focused_paper_ids=valid_ids)
            )

    return ThreadScopeResponse(
        paper_ids=valid_ids,
        papers=[
            IndexedPaper(
                paper_id=str(r["id"]),
                title=r["title"],
                summary=r.get("summary"),
                authors=r.get("authors"),
                categories=r.get("primary_category"),
                submitted_at=r.get("published"),
                link=r.get("link"),
                pdf_url=r.get("pdf_url"),
            )
            for r in papers
        ],
    )
