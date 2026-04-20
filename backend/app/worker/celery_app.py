"""Celery application configuration."""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from app.settings import get_settings

s = get_settings()

app = Celery("brieflyai", broker=s.celery_broker_url, backend=s.celery_result_backend)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("io", Exchange("io"), routing_key="io"),
        Queue("cpu", Exchange("cpu"), routing_key="cpu"),
    ),
    task_default_queue="io",
    task_default_exchange="io",
    task_default_routing_key="io",
    task_routes={
        "app.worker.tasks.fetch.fetch_papers": {"queue": "io"},
        "app.worker.tasks.ingest.ingest_documents": {"queue": "cpu"},
        "app.worker.tasks.summarize.summarize_chunks": {"queue": "io"},
        "app.worker.tasks.vectors.update_intent_vectors": {"queue": "io"},
        "app.worker.tasks.pipeline.run_daily_pipeline": {"queue": "io"},
    },
)

app.conf.update(include=[
    "app.worker.tasks.fetch",
    "app.worker.tasks.ingest",
    "app.worker.tasks.summarize",
    "app.worker.tasks.vectors",
    "app.worker.tasks.pipeline",
])

# Beat schedule (imported late to avoid circular imports)
from app.worker.schedules import beat_schedule  # noqa: E402

app.conf.beat_schedule = beat_schedule
