import os
import urllib.parse
from dotenv import load_dotenv

# Load .env
load_dotenv()

POSTGRES_USER = os.environ.get("POSTGRES_USER") or "postgres"
# URL-encode the password for asyncpg to avoid issues with special characters on some machines.
POSTGRES_PASSWORD = urllib.parse.quote_plus(
    os.environ.get("POSTGRES_PASSWORD") or "password"
)
POSTGRES_HOST = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
POSTGRES_PORT = os.environ.get("POSTGRES_PORT") or "5432"
POSTGRES_DB = os.environ.get("POSTGRES_DB") or "postgres"
ASYNC_DB_API = "asyncpg"
POSTGRES_USE_NULL_POOL= os.environ.get("POSTGRES_USE_NULL_POOL", "").lower() == "true"
POSTGRES_POOL_PRE_PING= os.environ.get("POSTGRES_POOL_PRE_PING", "").lower() == "true"
POSTGRES_POOL_RECYCLE=  60 * 20
POSTGRES_API_SERVER_POOL_SIZE = int(
    os.environ.get("POSTGRES_API_SERVER_POOL_SIZE") or 40
)
POSTGRES_API_SERVER_POOL_OVERFLOW = int(
    os.environ.get("POSTGRES_API_SERVER_POOL_OVERFLOW") or 10
)

POSTGRES_API_SERVER_READ_ONLY_POOL_SIZE = int(
    os.environ.get("POSTGRES_API_SERVER_READ_ONLY_POOL_SIZE") or 10
)
POSTGRES_API_SERVER_READ_ONLY_POOL_OVERFLOW = int(
    os.environ.get("POSTGRES_API_SERVER_READ_ONLY_POOL_OVERFLOW") or 5
)

