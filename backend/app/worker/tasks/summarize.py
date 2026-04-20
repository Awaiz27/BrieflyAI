"""Summarise un-summarised abstract chunks."""

from __future__ import annotations

import logging

from celery import shared_task

from app.worker.tasks._helpers import run_async

logger = logging.getLogger(__name__)


async def _summarize() -> None:
    from app.llm.summarizer import LLMSummarizer
    await LLMSummarizer().run()


@shared_task(
    bind=True,
    name="app.worker.tasks.summarize.summarize_chunks",
    queue="io",
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=3600,
    time_limit=3900,
)
def summarize_chunks(self) -> None:
    try:
        run_async(_summarize)()
    except Exception as exc:
        logger.exception("summarize_chunks failed")
        raise self.retry(exc=exc)
