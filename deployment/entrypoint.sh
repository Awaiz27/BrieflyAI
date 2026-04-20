#!/bin/sh
set -e

echo "Running Alembic migrations..."
alembic upgrade head

echo "Ensuring all tables exist..."
python -c "
from app.db.engine import AsyncSqlEngine
from app.db.models import Base
import asyncio

async def ensure_tables():
    await AsyncSqlEngine.init_engine()
    e = AsyncSqlEngine._engine
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await e.dispose()

asyncio.run(ensure_tables())
"
echo "Database ready."

echo ""
echo "Running startup health checks (STARTUP_ROLE=${STARTUP_ROLE:-api})..."
python -c "
from app.core.startup import run_startup_checks_sync
run_startup_checks_sync()
"

echo "✅ All pre-flight checks passed. Starting services..."
exec "$@"
