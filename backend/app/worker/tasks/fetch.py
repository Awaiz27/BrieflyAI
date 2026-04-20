"""Fetch papers from arXiv."""

from __future__ import annotations

import logging

from celery import shared_task

from app.settings import get_settings
from app.worker.tasks._helpers import run_async

logger = logging.getLogger(__name__)


async def _fetch(window: str) -> None:
    from app.services.scraper import FetcherConfig, PaperScraper

    s = get_settings()
    config = FetcherConfig(
        category=s.paper_api_category,
        max_results=s.paper_api_max_results,
        default_window=window,
        base_url=s.paper_api_base_url,
        http_timeout=s.paper_api_http_timeout,
        http_max_retries=s.paper_api_http_max_retries,
    )
    await PaperScraper(config).run()


@shared_task(
    bind=True,
    name="app.worker.tasks.fetch.fetch_papers",
    queue="io",
    max_retries=3,
    default_retry_delay=60,
)
def fetch_papers(self, window: str | None = None) -> None:
    s = get_settings()
    w = window or s.paper_api_default_window
    try:
        run_async(_fetch)(w)
    except Exception as exc:
        logger.exception("fetch_papers failed")
        raise self.retry(exc=exc)
