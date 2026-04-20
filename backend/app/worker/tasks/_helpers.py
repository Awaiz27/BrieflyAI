"""Shared helper for Celery tasks that need an async engine."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from app.db.engine import AsyncSqlEngine


async def _ensure_engine() -> None:
    """Idempotently initialise the engine inside a Celery worker."""
    try:
        AsyncSqlEngine.get_engine()
    except RuntimeError:
        from app.settings import get_settings
        s = get_settings()
        await AsyncSqlEngine.init_engine()


def run_async(coro_fn: Callable[..., Coroutine]) -> Callable:
    """Wrap an async function so Celery can call it synchronously."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        async def _run():
            await _ensure_engine()
            return await coro_fn(*args, **kwargs)
        return asyncio.run(_run())

    wrapper.__name__ = coro_fn.__name__
    return wrapper
