import logging
from collections.abc import AsyncGenerator
from fastapi import FastAPI
from docling.datamodel.accelerator_options import AcceleratorDevice
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from utils.logger import setup_logging, set_context
from configs.data_fetcher_config import (
    PAPER_API_CATEGORY,
    PAPER_API_MAX_RESULTS,
    PAPER_API_DEFAULT_WINDOW,
    PAPER_API_BASE_URL,
    PAPER_API_HTTP_MAX_RETRIES,
    PAPER_API_HTTP_TIMEOUT_SECONDS,
    )
from configs.db_config import (
    POSTGRES_API_SERVER_POOL_SIZE,
    POSTGRES_API_SERVER_POOL_OVERFLOW,
    POSTGRES_API_SERVER_READ_ONLY_POOL_SIZE,
    POSTGRES_API_SERVER_READ_ONLY_POOL_OVERFLOW,
    POSTGRES_USER, 
    POSTGRES_PASSWORD,

    )
from configs.constants import (
    OCR_BATCH_SIZE,
    LAYOUT_BATCH_SIZE,
    TABLE_BATCH_SIZE,
    CHUNK_MAX_TOKENS,
    CHUNK_MIN_TOKENS,
    MODEL_BASE_URL,
    EMBEDDER_MODEL_NAME,
    APP_HOST,
    APP_PORT
)
from db.engine.connection_warmup import warm_up_connections
from db.engine.sql_engine import AsyncSqlEngine
from services.scrape_and_store import PaperDataFetcher, DataFetcherAttributes
from llm_pipeline.doc_parser import DocumentIngestionPipeline, DocumentIngestConfig
import asyncio
import torch
import uvicorn
from db.update_intent_vectors import run_intent_vector_job

from api.routes import router

setup_logging(json_logs=True)

logger = logging.getLogger(__name__)

set_context(request_id="abc-123", job_id="job-789")

DEVICE = (
    AcceleratorDevice.CUDA
    if torch.cuda.is_available()
    else AcceleratorDevice.CPU
)

logger.info("App started successfully")
# logger.error("Something failed")

# asyncio.run(AsyncSqlEngine.init_engine(
#     pool_size=POSTGRES_API_SERVER_POOL_SIZE,
#     max_overflow=POSTGRES_API_SERVER_POOL_OVERFLOW,
#     app_name="main"
# ))


@asynccontextmanager
async def lifespan(app : FastAPI) -> AsyncGenerator[None, None]: #app: FastAPI

    try:
        await AsyncSqlEngine.init_engine(
            pool_size=POSTGRES_API_SERVER_POOL_SIZE,
            max_overflow=POSTGRES_API_SERVER_POOL_OVERFLOW,
            app_name="FASTAPI_Server"
        )
        AsyncSqlEngine.get_engine()

        await AsyncSqlEngine.init_readonly_engine(
            pool_size=POSTGRES_API_SERVER_READ_ONLY_POOL_SIZE,
            max_overflow=POSTGRES_API_SERVER_READ_ONLY_POOL_OVERFLOW,
            readonly_user=POSTGRES_USER, 
            readonly_password=POSTGRES_PASSWORD,
        )

        # fill up Postgres connection pools
        await warm_up_connections()

        yield
    
    finally:
        await AsyncSqlEngine.reset_engine()

def get_application():

    # connection = lifespan()
    
   

    # data_fetcher = PaperDataFetcher(
    #     settings=DataFetcherAttributes(
    #     category=PAPER_API_CATEGORY,
    #     max_results=PAPER_API_MAX_RESULTS,
    #     default_window=PAPER_API_DEFAULT_WINDOW,
    #     base_url=PAPER_API_BASE_URL,
    #     http_max_retries=PAPER_API_HTTP_MAX_RETRIES,
    #     http_timeout_seconds=PAPER_API_HTTP_TIMEOUT_SECONDS,
    #     )
    # )

    # data_fetcher.run()

    # doc_config = DocumentIngestConfig(
    # device=DEVICE,
    # ocr_batch_size=OCR_BATCH_SIZE,
    # layout_batch_size=LAYOUT_BATCH_SIZE,
    # table_batch_size=TABLE_BATCH_SIZE,
    # chunk_max_tokens=CHUNK_MAX_TOKENS,
    # chunk_min_tokens=CHUNK_MIN_TOKENS,
    # embedder_base_url=EMBEDDER_BASE_URL,
    # embedder_model_name=EMBEDDER_MODEL_NAME,
    # )

    # doc_ingest = DocumentIngestionPipeline(doc_config)
    # asyncio.run(doc_ingest.run())

    app = FastAPI(
    title="BrieflyAI Backend",
    version="1.0.0",
    description="BrieflyAI API for ranking, summarizing and chat with recent arXiv papers ",
    lifespan= lifespan,
    )

    app.include_router(router)

    # asyncio.run(run_intent_vector_job()) 

    return app


app = get_application()

@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    logger.info(
        f"Starting BrieflyAI Backend"
    )

    uvicorn.run(app, host=APP_HOST, port=APP_PORT)