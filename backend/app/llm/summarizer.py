"""LLM-based summarization of abstract chunks."""

from __future__ import annotations

import logging

from app.db.engine import get_session
from app.db.repositories.papers import (
    fetch_unsummarised_body_chunks,
    fetch_unsummarised_abstract_chunks,
    update_body_chunk_summaries,
    update_abstract_chunk_summaries,
)
from app.llm.clients import OllamaLLMClient
from app.settings import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a scholarly reviewer. Produce a concise yet comprehensive summary "
    "of a research paper's abstract. Capture objectives, methodology, key arguments, "
    "major findings, and conclusions. Rewrite in original language preserving meaning "
    "and academic tone."
)


class LLMSummarizer:
    def __init__(self, batch_size: int = 50, timeout: int = 120):
        s = get_settings()
        self._batch_size = batch_size
        self._client = OllamaLLMClient(
            base_url=s.model_base_url,
            model=s.llm_model_name,
            max_concurrency=s.llm_max_concurrency,
            system_prompt=_SYSTEM_PROMPT,
            timeout=timeout,
        )

    async def run(self) -> None:
        logger.info("Starting LLM summarization")
        async with self._client:
            while True:
                async with get_session() as session:
                    async with session.begin():
                        rows = await fetch_unsummarised_abstract_chunks(session, self._batch_size)
                if not rows:
                    logger.info("All abstract chunks summarised")
                    break

                prompts = [row["text"] for row in rows]
                summaries = await self._client.generate(prompts)
                updates = [{"id": row["id"], "llm_summary": s} for row, s in zip(rows, summaries)]

                async with get_session() as session:
                    async with session.begin():
                        await update_abstract_chunk_summaries(session, updates)
                logger.info("Summarised %d chunks", len(updates))

            while True:
                async with get_session() as session:
                    async with session.begin():
                        rows = await fetch_unsummarised_body_chunks(session, self._batch_size)
                if not rows:
                    logger.info("All body chunks summarised")
                    break

                prompts = [row["text"] for row in rows]
                summaries = await self._client.generate(prompts)
                updates = [{"id": row["id"], "llm_summary": s} for row, s in zip(rows, summaries)]

                async with get_session() as session:
                    async with session.begin():
                        await update_body_chunk_summaries(session, updates)
                logger.info("Summarised %d body chunks", len(updates))

        logger.info("LLM summarization completed")
