"""Daily ingestion pipeline: fetch → ingest → summarize → intent vectors."""

from __future__ import annotations

import logging

from celery import chain, shared_task

from app.settings import get_settings
from app.worker.tasks.fetch import fetch_papers
from app.worker.tasks.ingest import ingest_documents
from app.worker.tasks.summarize import summarize_chunks
from app.worker.tasks.vectors import update_intent_vectors

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.pipeline.run_daily_pipeline", queue="io")
def run_daily_pipeline(window: str | None = None) -> None:
    s = get_settings()
    w = window or s.paper_api_default_window
    logger.info("Launching daily pipeline (window=%s)", w)
    pipeline = chain(
        fetch_papers.si(window=w),
        ingest_documents.si(),
        summarize_chunks.si(),
        update_intent_vectors.si(),
    )
    pipeline.apply_async()
