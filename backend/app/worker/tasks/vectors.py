"""Update intent vectors (category + global centroids)."""

from __future__ import annotations

import logging

from celery import shared_task

from app.worker.tasks._helpers import run_async

logger = logging.getLogger(__name__)


async def _update() -> None:
    from app.db.engine import get_session
    from app.db.repositories.vectors import run_intent_vector_job

    async with get_session() as session:
        async with session.begin():
            await run_intent_vector_job(session)


@shared_task(
    bind=True,
    name="app.worker.tasks.vectors.update_intent_vectors",
    queue="io",
    max_retries=2,
    default_retry_delay=60,
)
def update_intent_vectors(self) -> None:
    try:
        run_async(_update)()
    except Exception as exc:
        logger.exception("update_intent_vectors failed")
        raise self.retry(exc=exc)
