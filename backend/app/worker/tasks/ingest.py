"""Ingest (chunk + embed) unprocessed papers."""

from __future__ import annotations

import logging

from celery import shared_task

from app.worker.tasks._helpers import run_async

logger = logging.getLogger(__name__)


async def _ingest() -> None:
    from app.llm.doc_parser import DocumentIngestionPipeline
    await DocumentIngestionPipeline().run()


@shared_task(
    bind=True,
    name="app.worker.tasks.ingest.ingest_documents",
    queue="cpu",
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=5400,
    time_limit=5700,
)
def ingest_documents(self) -> None:
    try:
        run_async(_ingest)()
    except Exception as exc:
        logger.exception("ingest_documents failed")
        raise self.retry(exc=exc)
