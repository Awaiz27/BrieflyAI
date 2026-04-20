"""Chat streaming route (SSE)."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import get_current_user
from app.api.schemas import SendMessageRequest
from app.db.engine import get_session
from app.db.models import ChatThread

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

# The agent is lazily imported to avoid circular deps at module load time
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from app.agent.graph import ResearchAgent
        raise RuntimeError("Agent not initialised")
    return _agent


def set_agent(agent) -> None:
    global _agent
    _agent = agent


@router.post("/threads/{chat_id}/messages")
async def send_message(
    chat_id: str,
    req: SendMessageRequest,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    async with get_session() as db:
        async with db.begin():
            thread = (
                await db.execute(
                    select(ChatThread.chat_id).where(ChatThread.chat_id == chat_id, ChatThread.user_id == user_id)
                )
            ).scalar_one_or_none()
    if not thread:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    agent = _get_agent()

    async def _stream() -> AsyncIterator[str]:
        answer = ""
        run_id = None
        try:
            async for payload in agent.run_stream(
                user_id=user_id,
                chat_id=chat_id,
                query=req.content,
                idempotency_key=req.idempotency_key,
                focused_paper_ids=req.paper_ids,
                thinking_mode=req.thinking_mode,
                llm_provider=req.llm_provider,
            ):
                run_id = payload.get("run_id")
                update = payload.get("update", {})
                for node_name, node_state in update.items():
                    draft = None
                    if isinstance(node_state, dict) and "draft" in node_state:
                        answer = node_state["draft"]
                        draft = node_state["draft"]
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "delta",
                                "seq": payload["seq"],
                                "node": node_name,
                                "draft": draft,
                            }
                        )
                        + "\n\n"
                    )
            yield f"data: {json.dumps({'type': 'done', 'answer': answer, 'run_id': run_id})}\n\n"
        except Exception as exc:
            logger.error("SSE stream error: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
