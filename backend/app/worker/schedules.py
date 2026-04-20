"""Celery Beat schedule definitions."""

from __future__ import annotations

from celery.schedules import crontab

from app.settings import get_settings

s = get_settings()

beat_schedule = {
    "daily-ingestion-pipeline": {
        "task": "app.worker.tasks.pipeline.run_daily_pipeline",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "io"},
        "kwargs": {"window": s.paper_api_default_window},
    },
}
