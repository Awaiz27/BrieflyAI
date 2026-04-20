"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.agent.graph import ResearchAgent
from app.api.routers import auth, chat, papers, researchers, threads
from app.api.routers.chat import set_agent
from app.core.logging import setup_logging
from app.db.engine import AsyncSqlEngine, get_session, warm_up_connections
from app.db.models import RPAbstractData
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def _maybe_seed_pipeline() -> None:
    """If no papers exist from today or yesterday, dispatch the daily pipeline."""
    async with get_session() as session:
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(RPAbstractData)
                    .where(RPAbstractData.created_at >= func.now() - timedelta(days=1))
                )
            ).scalar_one()

    if count == 0:
        logger.info("No recent papers found — triggering daily pipeline")
        from app.worker.tasks.pipeline import run_daily_pipeline
        run_daily_pipeline.delay()
    else:
        logger.info("Found %d recent papers — skipping seed", count)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    s = get_settings()
    agent: ResearchAgent | None = None
    logger.info("="*60)
    logger.info("APPLICATION STARTUP")
    logger.info("="*60)
    
    try:
        # Database
        logger.info("[Startup] Initializing database engine...")
        await AsyncSqlEngine.init_engine()
        await AsyncSqlEngine.init_readonly_engine()
        await warm_up_connections()
        logger.info("[Startup] ✓ Database initialized")

        # Agent
        logger.info("[Startup] Initializing research agent...")
        agent = ResearchAgent()
        await agent.startup()
        set_agent(agent)
        logger.info("[Startup] ✓ Agent initialized")

        # Seed data if DB is empty
        logger.info("[Startup] Checking for recent papers...")
        await _maybe_seed_pipeline()

        logger.info("="*60)
        logger.info("✓ APPLICATION STARTUP COMPLETE")
        logger.info("="*60)
        yield
    except Exception as e:
        logger.error(f"[Startup] ✗ STARTUP FAILED: {type(e).__name__}: {e}", exc_info=True)
        raise
    finally:
        logger.info("[Shutdown] Shutting down agent...")
        if agent is not None:
            await agent.shutdown()
        await AsyncSqlEngine.dispose()
        logger.info("[Shutdown] ✓ Application shutdown complete")


def create_app() -> FastAPI:
    s = get_settings()

    setup_logging(
        level=s.log_level,
        log_dir=s.log_dir,
        log_file=s.log_file_name,
        max_mb=s.log_max_size_mb,
        backup_count=s.log_backup_count,
    )

    app = FastAPI(
        title="BrieflyAI",
        version="2.0.0",
        description="AI-powered research paper discovery, ranking, and chat",
        lifespan=_lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(auth.router)
    app.include_router(papers.router)
    app.include_router(researchers.router)
    app.include_router(threads.router)
    app.include_router(chat.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
