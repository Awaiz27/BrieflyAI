import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any
from typing import AsyncContextManager
from sqlalchemy import URL
from sqlalchemy import pool
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from configs.db_config import (POSTGRES_USER, 
                               POSTGRES_PASSWORD,
                               POSTGRES_HOST,
                               POSTGRES_PORT,
                               POSTGRES_USER,
                               POSTGRES_DB,
                               ASYNC_DB_API,
                               POSTGRES_USE_NULL_POOL,
                               POSTGRES_POOL_PRE_PING,
                               POSTGRES_POOL_RECYCLE,)

import logging 

logger = logging.getLogger(__name__)


def add_app_name(url_obj: URL, app_name: str) -> URL:
    query = dict(url_obj.query or {})
    query["application_name"] = app_name

    new_url = url_obj.set(query=query)
    return new_url


def build_connection_string(
    *,
    db_api: str | None = ASYNC_DB_API,
    user: str = POSTGRES_USER,
    password: str = POSTGRES_PASSWORD,
    host: str = POSTGRES_HOST,
    port: str = POSTGRES_PORT,
    db: str = POSTGRES_DB,
    app_name: str | None = None,
) -> str:
    
    database_driver = "postgresql" if db_api is None else f"postgresql+{db_api}"
    url_obj  = URL.create(
        drivername=database_driver,
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=db,
    )

    # asyncpg adds its own application_name; skip if using that driver
    if app_name is not None and db_api != "asyncpg":
        url_obj = add_app_name(url_obj , app_name)

    url_str = url_obj.render_as_string(hide_password=False)

    # Escape % for ConfigParser (ConfigParser treats % as interpolation)
    escaped_url = url_str.replace("%", "%%")

    return escaped_url


class AsyncSqlEngine:
    _engine: AsyncEngine | None = None
    _readonly_engine: AsyncEngine | None = None

    _lock = asyncio.Lock()
    _readonly_lock = asyncio.Lock()

    @classmethod
    async def init_engine(
        cls,
        pool_size: int,
        max_overflow: int,
        app_name: str | None = None,
        db_api: str = ASYNC_DB_API,
        connection_string: str | None = None,
        **extra_engine_kwargs: Any,
    ) -> None:
        async with cls._lock:
            if cls._engine:
                return

            if not connection_string:
                connection_string = build_connection_string(db_api=db_api, app_name=app_name + "_async",)

            final_engine_kwargs = cls._build_engine_kwargs(
                pool_size, max_overflow, extra_engine_kwargs
            )

            logger.info(
                "Creating primary async SQLAlchemy engine",
                extra={
                    "pool_size": final_engine_kwargs.get("pool_size"),
                    "max_overflow": final_engine_kwargs.get("max_overflow"),
                    "null_pool": POSTGRES_USE_NULL_POOL,
                },
            )

            cls._engine = create_async_engine(
                connection_string,
                **final_engine_kwargs,
            )

    @classmethod
    async def init_readonly_engine(
        cls,
        pool_size: int,
        max_overflow: int,
        readonly_user: str,
        readonly_password: str,
        **extra_engine_kwargs: Any,
    ) -> None:
        async with cls._readonly_lock:
            if cls._readonly_engine:
                return

            if not readonly_user or not readonly_password:
                raise ValueError("Readonly credentials missing")

            connection_string = build_connection_string(
                user=readonly_user,
                password=readonly_password,
            )

            final_engine_kwargs = cls._build_engine_kwargs(
                pool_size, max_overflow, extra_engine_kwargs
            )

            logger.info(
                "Creating readonly async SQLAlchemy engine",
                extra={
                    "pool_size": final_engine_kwargs.get("pool_size"),
                    "max_overflow": final_engine_kwargs.get("max_overflow"),
                    "null_pool": POSTGRES_USE_NULL_POOL,
                },
            )

            cls._readonly_engine = create_async_engine(
                connection_string,
                **final_engine_kwargs,
            )

    @classmethod
    def _build_engine_kwargs(
        cls,
        pool_size: int,
        max_overflow: int,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        if POSTGRES_USE_NULL_POOL:
            kwargs = {"poolclass": pool.NullPool}
            kwargs.update(extra)
            kwargs.pop("pool_size", None)
            kwargs.pop("max_overflow", None)
            return kwargs

        kwargs = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_pre_ping": POSTGRES_POOL_PRE_PING,
            "pool_recycle": POSTGRES_POOL_RECYCLE,
        }
        kwargs.update(extra)
        return kwargs

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        if not cls._engine:
            raise RuntimeError("Async engine not initialized.")
        return cls._engine

    @classmethod
    def get_readonly_engine(cls) -> AsyncEngine:
        if not cls._readonly_engine:
            raise RuntimeError("Readonly async engine not initialized.")
        return cls._readonly_engine

    @classmethod
    async def reset_engine(cls):
        if cls._engine:
            await cls._engine.dispose()
            cls._engine = None
        if cls._readonly_engine:
            await cls._readonly_engine.dispose()
            cls._readonly_engine = None

def get_sqlalchemy_async_engine() -> Engine:
    return AsyncSqlEngine.get_engine()


def get_readonly_sqlalchemy_engine() -> Engine:
    return AsyncSqlEngine.get_readonly_engine()

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:

    engine = get_sqlalchemy_async_engine()

    # Create connection with schema translation to handle querying the right schema
    schema_translate_map = {}
    async with engine.connect() as connection:
        connection = await connection.execution_options(
            schema_translate_map=schema_translate_map
        )
        async with AsyncSession(
            bind=connection, expire_on_commit=False
        ) as async_session:
            yield async_session


# def get_async_session_context_manager(
# ) -> AsyncContextManager[AsyncSession]:
#     return asynccontextmanager(get_async_session)



