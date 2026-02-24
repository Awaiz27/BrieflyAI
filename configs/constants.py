import os
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "application.log")

MAX_LOG_SIZE_MB = 10
BACKUP_COUNT = 10

IO_CONCURRENCY = 20
CPU_WORKERS = 2

# PDF pipeline batching
OCR_BATCH_SIZE: int = 2
LAYOUT_BATCH_SIZE: int = 16
TABLE_BATCH_SIZE: int = 2

# Chunking
CHUNK_MAX_TOKENS: int = 5000
CHUNK_MIN_TOKENS: int = 500

# LLM and Embedding
MODEL_BASE_URL: str = "http://localhost:11434"
EMBEDDER_MODEL_NAME: str = "qwen3-embedding:0.6b-fp16"
LLM_MODEL_NAME : str = "qwen3:0.6b-q4_K_M"

#APP
APP_HOST: str = os.environ.get("APP_HOST") or  "localhost"
APP_PORT: int = int(os.environ.get("APP_PORT")) or 9000

#Ranking
DEFAULT_OVERFETCH_MULTIPLIER: int = 3
MAX_TOP_K: int = 200
# How much history to use for centroids
CATEGORY_LOOKBACK_DAYS : int = 90
GLOBAL_LOOKBACK_DAYS : int = 180
# Safety: skip categories with too little data
MIN_CHUNKS_PER_CATEGORY : int = 1
CHUNK_RECALL_LIMIT : int = 2000
RECENCY_WEIGHT : float = 0.3 
