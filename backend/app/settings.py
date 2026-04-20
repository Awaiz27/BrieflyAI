"""
Centralised application settings using pydantic-settings.

Every configurable value lives here. Modules import ``get_settings()`` instead
of reading env vars directly, which makes testing and overriding trivial.
"""

from __future__ import annotations

import urllib.parse
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "BrieflyAI"
    app_host: str = "0.0.0.0"
    app_port: int = 9001
    debug: bool = False
    log_level: str = "INFO"
    log_dir: str = "./logs"
    log_file_name: str = "application.log"
    log_max_size_mb: int = 10
    log_backup_count: int = 10

    # ── Postgres ─────────────────────────────────────────────────────────
    postgres_user: str = "postgres"
    postgres_password: str = "password"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "postgres"
    postgres_schema: str = "BrieflyAI"
    postgres_async_driver: str = "asyncpg"
    postgres_use_null_pool: bool = False
    postgres_pool_pre_ping: bool = False
    postgres_pool_recycle: int = 1200  # 20 minutes

    postgres_api_pool_size: int = 40
    postgres_api_pool_overflow: int = 10
    postgres_readonly_pool_size: int = 10
    postgres_readonly_pool_overflow: int = 5
    postgres_warmup_connections: int = 20

    # ── JWT / Auth ───────────────────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 72

    # ── LLM Provider ─────────────────────────────────────────────────────
    # Set LLM_PROVIDER=groq to use Groq cloud; anything else uses Ollama.
    llm_provider: str = "ollama"  # "ollama" | "groq"

    # ── Groq ─────────────────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_llm_model_name: str = "openai/gpt-oss-20b"

    # ── Ollama / LLM ────────────────────────────────────────────────────
    model_base_url: str = "http://localhost:11434"
    embedder_model_name: str = "qwen3-embedding:0.6b-fp16"
    llm_model_name: str = "qwen3:0.6b-q4_K_M"
    embedding_vector_dim: int = Field(default=1024, ge=1)
    embedder_max_concurrency: int = 2
    llm_max_concurrency: int = 4
    agent_llm_timeout_seconds: int = 120
    agent_rewrite_history_k: int = 10
    agent_compose_history_k: int = 10
    agent_summary_every_n_messages: int = 15
    agent_summary_max_chars: int = 3000
    agent_enable_review: bool = True
    agent_max_retries: int = 2
    agent_rag_expand_n: int = 3
    agent_rag_per_query_k: int = 10
    agent_rag_fused_k: int = 8
    agent_rag_final_k: int = 5
    agent_rrf_k: int = 60

    # ── arXiv Fetcher ────────────────────────────────────────────────────
    paper_api_category: str = "cs.*"
    paper_api_max_results: int = 100
    paper_api_default_window: str = "4d"
    paper_api_base_url: str = "https://export.arxiv.org/api/query"
    paper_api_http_timeout: int = 10
    paper_api_http_max_retries: int = 3

    # ── Document Ingestion ───────────────────────────────────────────────
    ocr_batch_size: int = 2
    layout_batch_size: int = 16
    table_batch_size: int = 2
    chunk_max_tokens: int = 5000
    chunk_min_tokens: int = 500
    io_concurrency: int = 20
    cpu_workers: int = 2
    ingest_max_parallel_papers: int = 1

    # ── Ranking ──────────────────────────────────────────────────────────
    default_overfetch_multiplier: int = 3
    max_top_k: int = 200
    category_lookback_days: int = 90
    global_lookback_days: int = 180
    min_chunks_per_category: int = 1
    chunk_recall_limit: int = 2000
    recency_weight: float = 0.3

    # ── Celery ───────────────────────────────────────────────────────────
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/0"

    # ── Computed helpers ─────────────────────────────────────────────────

    @property
    def encoded_password(self) -> str:
        return urllib.parse.quote_plus(self.postgres_password)

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+{self.postgres_async_driver}://"
            f"{self.postgres_user}:{self.encoded_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://"
            f"{self.postgres_user}:{self.encoded_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def psycopg_database_url(self) -> str:
        """psycopg v3 URL required by LangGraph AsyncPostgresSaver."""
        return (
            f"postgresql://"
            f"{self.postgres_user}:{self.encoded_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
