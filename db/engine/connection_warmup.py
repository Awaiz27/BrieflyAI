from sqlalchemy import text

from db.engine.sql_engine import get_sqlalchemy_async_engine

async def warm_up_connections(
   async_connections_to_warm_up: int = 20
) -> None:
    
    async_postgres_engine = get_sqlalchemy_async_engine()
    async_connections = [
        await async_postgres_engine.connect()
        for _ in range(async_connections_to_warm_up)
    ]
    for async_conn in async_connections:
        await async_conn.execute(text("SELECT 1"))
    for async_conn in async_connections:
        await async_conn.close()