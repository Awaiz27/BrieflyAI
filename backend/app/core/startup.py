"""Startup health checks and startup-time data reconciliation."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import NoReturn

from sqlalchemy import exists, select, text

from app.db.engine import AsyncSqlEngine, get_session
from app.db.models import AppUser, ChatThread, Chunk, IntentVector, PaperAbstractChunk, RPAbstractData
from app.db.repositories.papers import insert_papers
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def initialize_startup_engines() -> bool:
    """Initialize database engines required by startup checks."""
    try:
        await AsyncSqlEngine.init_engine()
        logger.info("✓ Primary startup engine initialized")
        try:
            await AsyncSqlEngine.init_readonly_engine()
            logger.info("✓ Read-only startup engine initialized")
        except Exception as exc:
            logger.warning("⚠ Read-only startup engine initialization failed: %s", exc)
        return True
    except Exception as exc:
        logger.error("✗ Startup engine initialization failed: %s", exc)
        return False


async def verify_database_connection() -> bool:
    """Verify database is accessible and responding."""
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        logger.info("✓ Database connection verified")
        return True
    except Exception as exc:
        logger.error("✗ Database connection failed: %s", exc)
        return False


async def verify_schema_exists() -> bool:
    """Verify required schema exists."""
    try:
        schema_name = get_settings().postgres_schema
        async with get_session() as session:
            result = await session.execute(
                text(
                    "SELECT schema_name "
                    "FROM information_schema.schemata "
                    "WHERE schema_name = :schema_name"
                ),
                {"schema_name": schema_name},
            )
        if result.scalar_one_or_none():
            logger.info("✓ Schema '%s' exists", schema_name)
            return True
        logger.error("✗ Schema '%s' not found", schema_name)
        return False
    except Exception as exc:
        logger.error("✗ Schema verification failed: %s", exc)
        return False


async def verify_required_tables() -> bool:
    """Verify critical tables exist before services start."""
    required_tables = [
        AppUser.__tablename__,
        RPAbstractData.__tablename__,
        PaperAbstractChunk.__tablename__,
        ChatThread.__tablename__,
        IntentVector.__tablename__,
    ]

    try:
        schema = get_settings().postgres_schema
        async with get_session() as session:
            for table_name in required_tables:
                result = await session.execute(
                    text(
                        "SELECT table_name "
                        "FROM information_schema.tables "
                        "WHERE table_schema = :schema_name AND table_name = :table_name"
                    ),
                    {"schema_name": schema, "table_name": table_name},
                )
                if not result.scalar_one_or_none():
                    logger.error("✗ Required table '%s' not found", table_name)
                    return False

        logger.info("✓ All %d required tables exist", len(required_tables))
        return True
    except Exception as exc:
        logger.error("✗ Table verification failed: %s", exc)
        return False


async def verify_connection_pools() -> bool:
    """Verify async connection pools are initialized and healthy."""
    try:
        async with AsyncSqlEngine.get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("✓ Main connection pool healthy")

        try:
            async with AsyncSqlEngine.get_readonly_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✓ Read-only connection pool healthy")
        except Exception:
            logger.warning("⚠ Read-only connection pool not yet initialized (will be lazy-loaded)")

        return True
    except Exception as exc:
        logger.error("✗ Connection pool verification failed: %s", exc)
        return False


def _build_fetcher_config(window: str):
    from app.services.scraper import FetcherConfig

    settings = get_settings()
    return FetcherConfig(
        category=settings.paper_api_category,
        max_results=settings.paper_api_max_results,
        default_window=window,
        base_url=settings.paper_api_base_url,
        http_timeout=settings.paper_api_http_timeout,
        http_max_retries=settings.paper_api_http_max_retries,
    )


async def reconcile_window_papers() -> bool:
    """Fetch the configured API window and insert only papers missing from the DB."""
    try:
        from app.services.scraper import PaperScraper

        window = get_settings().paper_api_default_window
        scraper = PaperScraper(_build_fetcher_config(window))

        entries = await asyncio.to_thread(scraper._fetch_all)
        if not entries:
            logger.warning("⚠ arXiv returned no papers for window '%s'", window)
            return True

        rows = scraper._transform(entries)
        api_links = sorted({row["link"] for row in rows if row.get("link")})
        existing_links: set[str] = set()
        missing_rows: list[dict] = []

        async with get_session() as session:
            async with session.begin():
                if api_links:
                    existing_links = set(
                        (
                            await session.execute(
                                select(RPAbstractData.link).where(RPAbstractData.link.in_(api_links))
                            )
                        ).scalars().all()
                    )
                missing_rows = [
                    row for row in rows if row.get("link") and row["link"] not in existing_links
                ]
                if missing_rows:
                    await insert_papers(session, missing_rows)

        logger.info(
            "✓ API reconciliation complete for %s: api=%d existing=%d inserted=%d",
            window,
            len(rows),
            len(existing_links),
            len(missing_rows),
        )
        return True
    except Exception as exc:
        logger.error("✗ API reconciliation failed: %s", exc)
        return False


async def _count_missing_body_chunks() -> int:
    async with get_session() as session:
        result = await session.execute(
            select(RPAbstractData.id).where(
                ~exists(select(1).where(Chunk.rp_abstract_id == RPAbstractData.id))
            )
        )
        return len(result.scalars().all())


async def _count_missing_abstract_chunks() -> int:
    async with get_session() as session:
        result = await session.execute(
            select(RPAbstractData.id).where(
                ~exists(select(1).where(PaperAbstractChunk.rp_abstract_id == RPAbstractData.id))
            )
        )
        return len(result.scalars().all())


async def reconcile_chunk_data() -> bool:
    """Detect papers with missing chunks and queue the ingestion pipeline.

    Body chunk ingestion (PDF download + parse + embed) is expensive and can
    take minutes per paper. Running it inline in entrypoint.sh would block the
    container from starting. Instead we just check whether a gap exists and, if
    so, dispatch the existing Celery tasks so workers handle the heavy lifting in
    the background.
    """
    try:
        missing_body = await _count_missing_body_chunks()
        missing_abstract = await _count_missing_abstract_chunks()

        logger.info(
            "Chunk gap check: missing_body=%d  missing_abstract=%d",
            missing_body,
            missing_abstract,
        )

        if missing_body > 0 or missing_abstract > 0:
            # Queue the full ingestion pipeline so workers process the backlog.
            # ingest_documents   → builds body chunks  (cpu queue)
            # summarize_chunks   → LLM summaries       (io queue)
            # update_intent_vectors → vector index     (io queue)
            from app.worker.tasks.ingest import ingest_documents
            from app.worker.tasks.summarize import summarize_chunks
            from app.worker.tasks.vectors import update_intent_vectors
            from celery import chain

            pipeline = chain(
                ingest_documents.si(),
                summarize_chunks.si(),
                update_intent_vectors.si(),
            )
            pipeline.apply_async()
            logger.warning(
                "⚠ Queued ingestion pipeline to backfill %d body / %d abstract chunk gaps. "
                "Workers will process in the background.",
                missing_body,
                missing_abstract,
            )
        else:
            logger.info("✓ No chunk gaps found.")

        return True
    except Exception as exc:
        logger.error("✗ Chunk reconciliation failed: %s", exc)
        return False


async def verify_broker_reachable() -> bool:
    """Verify Celery broker (RabbitMQ) is reachable via kombu."""
    try:
        from kombu import Connection
        broker_url = get_settings().celery_broker_url
        with Connection(broker_url) as conn:
            conn.ensure_connection(max_retries=3, interval_start=1, interval_step=1)
        logger.info("✓ Celery broker reachable")
        return True
    except Exception as exc:
        logger.error("✗ Celery broker not reachable: %s", exc)
        return False


async def verify_redis_reachable() -> bool:
    """Verify Redis result backend is reachable."""
    try:
        import redis as redis_lib
        redis_url = get_settings().celery_result_backend
        client = redis_lib.from_url(redis_url, socket_connect_timeout=5)
        client.ping()
        logger.info("✓ Redis result backend reachable")
        return True
    except Exception as exc:
        logger.error("✗ Redis not reachable: %s", exc)
        return False


async def _run_checks(role: str, checks: list[tuple[str, any]]) -> bool:
    """Execute a named list of checks and log a summary."""
    logger.info("=" * 60)
    logger.info("Startup Checks — Role: %s", role.upper())
    logger.info("=" * 60)

    results: list[tuple[str, bool]] = []
    for check_name, check_func in checks:
        logger.info("\n[%s] Running...", check_name)
        try:
            results.append((check_name, await check_func()))
        except Exception:
            logger.exception("✗ %s check crashed", check_name)
            results.append((check_name, False))

    logger.info("\n%s", "=" * 60)
    logger.info("Results")
    logger.info("%s", "=" * 60)
    all_passed = True
    for check_name, passed in results:
        logger.info("%s: %s", "✓ PASS" if passed else "✗ FAIL", check_name)
        if not passed:
            all_passed = False
    logger.info("%s\n", "=" * 60)
    return all_passed


async def run_api_checks() -> bool:
    """Full checks for the API service: infra + schema + data reconciliation."""
    return await _run_checks("api", [
        ("Engine Initialization",       initialize_startup_engines),
        ("Database Connection",         verify_database_connection),
        ("Schema Existence",            verify_schema_exists),
        ("Required Tables",             verify_required_tables),
        ("Connection Pools",            verify_connection_pools),
        ("Broker Reachable",            verify_broker_reachable),
        ("Redis Reachable",             verify_redis_reachable),
        ("API Window Reconciliation",   reconcile_window_papers),
        ("Chunk Reconciliation",        reconcile_chunk_data),
    ])


async def run_worker_io_checks() -> bool:
    """Checks for the IO Celery worker: DB + broker reachability."""
    return await _run_checks("celery_worker_io", [
        ("Engine Initialization",   initialize_startup_engines),
        ("Database Connection",     verify_database_connection),
        ("Required Tables",         verify_required_tables),
        ("Broker Reachable",        verify_broker_reachable),
        ("Redis Reachable",         verify_redis_reachable),
    ])


async def run_worker_cpu_checks() -> bool:
    """Checks for the CPU Celery worker: DB + broker reachability."""
    return await _run_checks("celery_worker_cpu", [
        ("Engine Initialization",   initialize_startup_engines),
        ("Database Connection",     verify_database_connection),
        ("Required Tables",         verify_required_tables),
        ("Broker Reachable",        verify_broker_reachable),
        ("Redis Reachable",         verify_redis_reachable),
    ])


async def run_beat_checks() -> bool:
    """Checks for Celery Beat: broker + redis only (beat doesn't query the DB)."""
    return await _run_checks("celery_beat", [
        ("Broker Reachable",    verify_broker_reachable),
        ("Redis Reachable",     verify_redis_reachable),
    ])


_ROLE_CHECKS = {
    "api":               run_api_checks,
    "celery_beat":       run_beat_checks,
    "celery_worker_io":  run_worker_io_checks,
    "celery_worker_cpu": run_worker_cpu_checks,
}


async def startup_or_die() -> NoReturn:
    """Run role-based startup checks and terminate the process on failure."""
    import os
    role = os.environ.get("STARTUP_ROLE", "api").lower()
    check_fn = _ROLE_CHECKS.get(role)
    if check_fn is None:
        logger.error("Unknown STARTUP_ROLE='%s'. Valid roles: %s", role, list(_ROLE_CHECKS))
        raise SystemExit(1)

    passed = await check_fn()
    if not passed:
        logger.critical("Startup checks failed. Exiting.")
        raise SystemExit(1)

    logger.info("All startup checks passed. Services ready to start.\n")
    raise SystemExit(0)


def run_startup_checks_sync() -> None:
    """Synchronous wrapper used by the container entrypoint.

    Reads STARTUP_ROLE from the environment (default: 'api') and runs the
    corresponding role-based checks.
    """
    import os
    role = os.environ.get("STARTUP_ROLE", "api").lower()
    check_fn = _ROLE_CHECKS.get(role)
    if check_fn is None:
        logger.error("Unknown STARTUP_ROLE='%s'. Valid roles: %s", role, list(_ROLE_CHECKS))
        raise SystemExit(1)
    passed = asyncio.run(check_fn())
    if not passed:
        raise SystemExit(1)
