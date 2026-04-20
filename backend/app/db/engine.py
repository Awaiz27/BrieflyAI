"""
Async SQLAlchemy engine management.

Provides a singleton ``AsyncSqlEngine`` that is initialised once during
application startup and used everywhere via ``get_session()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import URL, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.settings import get_settings

logger = logging.getLogger(__name__)


# ── URL helpers ─────────────────────────────────────────────────────────────


def _build_url(
    *,
    user: str | None = None,
    password: str | None = None,
    app_name: str | None = None,
    schema: str | None = None,
) -> str:
    s = get_settings()
    url_obj = URL.create(
        drivername=f"postgresql+{s.postgres_async_driver}",
        username=user or s.postgres_user,
        password=password or s.encoded_password,
        host=s.postgres_host,
        port=s.postgres_port,
        database=s.postgres_db,
    )
    query: dict[str, str] = {}
    if schema:
        query["options"] = f"-csearch_path={schema}"
    if app_name and s.postgres_async_driver != "asyncpg":
        query["application_name"] = app_name
    if query:
        url_obj = url_obj.set(query=query)
    return url_obj.render_as_string(hide_password=False)


def _engine_kwargs(s, pool_size: int, max_overflow: int, extra: dict) -> dict:
    if s.postgres_use_null_pool:
        kw: dict[str, Any] = {"poolclass": pool.NullPool}
        kw.update(extra)
        kw.pop("pool_size", None)
        kw.pop("max_overflow", None)
        return kw
    kw = {
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_pre_ping": s.postgres_pool_pre_ping,
        "pool_recycle": s.postgres_pool_recycle,
    }
    kw.update(extra)
    return kw


# ── Singleton ───────────────────────────────────────────────────────────────


class AsyncSqlEngine:
    _engine: AsyncEngine | None = None
    _readonly_engine: AsyncEngine | None = None
    _lock = asyncio.Lock()
    _ro_lock = asyncio.Lock()

    @classmethod
    async def init_engine(cls, **extra: Any) -> None:
        async with cls._lock:
            if cls._engine:
                return
            s = get_settings()
            url = _build_url(app_name="brieflyai_api")
            kw = _engine_kwargs(s, s.postgres_api_pool_size, s.postgres_api_pool_overflow, extra)
            cls._engine = create_async_engine(url, **kw)
            logger.info("Primary async engine created (pool=%s)", kw.get("pool_size"))

    @classmethod
    async def init_readonly_engine(cls, **extra: Any) -> None:
        async with cls._ro_lock:
            if cls._readonly_engine:
                return
            s = get_settings()
            url = _build_url(user=s.postgres_user, password=s.encoded_password)
            kw = _engine_kwargs(s, s.postgres_readonly_pool_size, s.postgres_readonly_pool_overflow, extra)
            cls._readonly_engine = create_async_engine(url, **kw)
            logger.info("Readonly async engine created (pool=%s)", kw.get("pool_size"))

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        if not cls._engine:
            raise RuntimeError("Async engine not initialised — call init_engine() first")
        return cls._engine

    @classmethod
    def get_readonly_engine(cls) -> AsyncEngine:
        if not cls._readonly_engine:
            raise RuntimeError("Readonly engine not initialised")
        return cls._readonly_engine

    @classmethod
    async def dispose(cls) -> None:
        if cls._engine:
            await cls._engine.dispose()
            cls._engine = None
        if cls._readonly_engine:
            await cls._readonly_engine.dispose()
            cls._readonly_engine = None


# ── Session factory ─────────────────────────────────────────────────────────


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    engine = AsyncSqlEngine.get_engine()
    async with engine.connect() as conn:
        async with AsyncSession(bind=conn, expire_on_commit=False) as session:
            yield session


# ── Warmup ──────────────────────────────────────────────────────────────────


async def warm_up_connections(n: int | None = None) -> None:
    s = get_settings()
    count = n or s.postgres_warmup_connections
    engine = AsyncSqlEngine.get_engine()
    conns = [await engine.connect() for _ in range(count)]
    for c in conns:
        await c.execute(text("SELECT 1"))
    for c in conns:
        await c.close()
    logger.info("Warmed up %d connections", count)


# ── Sync URL for Alembic ───────────────────────────────────────────────────


def build_sync_url(app_name: str = "alembic") -> str:
    s = get_settings()
    url_obj = URL.create(
        drivername="postgresql",
        username=s.postgres_user,
        password=s.encoded_password,
        host=s.postgres_host,
        port=s.postgres_port,
        database=s.postgres_db,
    )
    rendered = url_obj.render_as_string(hide_password=False)
    return rendered.replace("%", "%%")
