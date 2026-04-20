"""arXiv paper scraper with pagination and error handling."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import feedparser
import requests
from dateutil.relativedelta import relativedelta

from app.db.engine import get_session
from app.db.repositories.papers import insert_papers
from app.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetcherConfig:
    category: str = "cs.*"
    max_results: int = 100
    default_window: str = "4d"
    sort_by: str = "submittedDate"
    sort_order: str = "descending"
    base_url: str = "https://export.arxiv.org/api/query"
    http_timeout: int = 10
    http_max_retries: int = 3


class PaperScraper:
    def __init__(self, config: FetcherConfig | None = None):
        s = get_settings()
        self._cfg = config or FetcherConfig(
            category=s.paper_api_category,
            max_results=s.paper_api_max_results,
            default_window=s.paper_api_default_window,
            base_url=s.paper_api_base_url,
            http_timeout=s.paper_api_http_timeout,
            http_max_retries=s.paper_api_http_max_retries,
        )

    # ── Window parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_window(window: str) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        if len(window) < 2:
            raise ValueError("Window must be like '1d', '2w', '3m'")
        num_str, unit = window[:-1], window[-1].lower()
        if not num_str.isdigit():
            raise ValueError(f"Invalid window: {window}")
        value = int(num_str)
        if unit == "d":
            delta = timedelta(days=value)
        elif unit == "w":
            delta = timedelta(weeks=value)
        elif unit == "m":
            delta = relativedelta(months=value)
        else:
            raise ValueError(f"Invalid unit '{unit}'. Use d/w/m.")
        start = now - delta
        return start.strftime("%Y%m%d"), now.strftime("%Y%m%d")

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return None

    # ── HTTP ────────────────────────────────────────────────────────────

    def _http_get(self, params: dict[str, Any]) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._cfg.http_max_retries + 1):
            try:
                resp = requests.get(
                    self._cfg.base_url,
                    params=params,
                    timeout=self._cfg.http_timeout,
                )
                if resp.status_code == 200:
                    return resp.text
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
            except Exception as exc:
                last_exc = exc
                logger.warning("arXiv request failed (attempt %d): %s", attempt, exc)
            time.sleep(min(2 ** attempt, 5))
        raise RuntimeError("arXiv API failed after retries") from last_exc

    # ── Fetch ───────────────────────────────────────────────────────────

    def _fetch_all(self) -> list[Any]:
        start_date, end_date = self._parse_window(self._cfg.default_window)
        query = f"cat:{self._cfg.category} AND submittedDate:[{start_date} TO {end_date}]"
        logger.info("Fetching arXiv %s → %s category=%s", start_date, end_date, self._cfg.category)

        entries: list[Any] = []
        start_idx = 0
        while True:
            params = {
                "search_query": query,
                "start": start_idx,
                "max_results": self._cfg.max_results,
                "sortBy": self._cfg.sort_by,
                "sortOrder": self._cfg.sort_order,
            }
            xml = self._http_get(params)
            feed_entries = feedparser.parse(xml).entries or []
            logger.info("Page start=%d returned %d entries", start_idx, len(feed_entries))
            if not feed_entries:
                break
            entries.extend(feed_entries)
            if len(feed_entries) < self._cfg.max_results:
                break
            start_idx += self._cfg.max_results

        logger.info("Total fetched: %d entries", len(entries))
        return entries

    # ── Transform ───────────────────────────────────────────────────────

    def _transform(self, entries: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for e in entries:
            authors = [getattr(a, "name", "") for a in getattr(e, "authors", []) if getattr(a, "name", None)]
            pdf_url = None
            for link in getattr(e, "links", []):
                if getattr(link, "type", None) == "application/pdf":
                    pdf_url = getattr(link, "href", None)
                    break
            pc = getattr(e, "arxiv_primary_category", None)
            primary_category = None
            if pc:
                primary_category = pc.get("term") if isinstance(pc, dict) else getattr(pc, "term", None)
            cats = []
            for t in getattr(e, "tags", []):
                term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
                if term:
                    cats.append(term)
            rows.append({
                "title": getattr(e, "title", None),
                "summary": getattr(e, "summary", None),
                "authors": ", ".join(authors),
                "published": self._to_datetime(getattr(e, "published", None)),
                "updated": self._to_datetime(getattr(e, "updated", None)),
                "link": getattr(e, "link", None),
                "pdf_url": pdf_url,
                "primary_category": primary_category,
                "all_categories": ", ".join(cats),
                "doi": getattr(e, "arxiv_doi", None),
                "journal_ref": getattr(e, "arxiv_journal_ref", None),
                "comment": getattr(e, "arxiv_comment", None),
            })
        return rows

    # ── Run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        entries = self._fetch_all()
        if not entries:
            logger.warning("No papers returned from arXiv API")
            return
        rows = self._transform(entries)
        async with get_session() as session:
            async with session.begin():
                await insert_papers(session, rows)
        logger.info("Stored %d papers", len(rows))
