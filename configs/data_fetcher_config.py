import os
import urllib.parse
from dotenv import load_dotenv
from typing import List

# Load .env
load_dotenv()

PAPER_API_CATEGORY: str = os.environ.get("PAPER_API_CATEGORY") or "cs.*"
PAPER_API_MAX_RESULTS: int = os.environ.get("PAPER_API_MAX_RESULTS") or 100
PAPER_API_DEFAULT_WINDOW: str = os.environ.get("PAPER_API_DEFAULT_WINDOW") or "4d"
PAPER_API_BASE_URL: List[str] = os.environ.get("PAPER_API_BASE_URL") or ["https://export.arxiv.org/api/query"]
PAPER_API_HTTP_TIMEOUT_SECONDS: int = os.environ.get("PAPER_API_HTTP_TIMEOUT_SECONDS") or 10
PAPER_API_HTTP_MAX_RETRIES: int = os.environ.get("PAPER_API_HTTP_MAX_RETRIES") or 3
