import logging
import time
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import pandas as pd
import requests
from pydantic import BaseModel
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, field
from db.research_paper_data import insert_rp_data

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class DataFetcherAttributes:
    category: str = field(default="cs.*") # arXiv category pattern
    max_results: int = field(default=100) # Max results per API page
    default_window: str = field(default="1d") # Default time window, e.g. 1d, 1w, 1m
    sort_by: str = field(default="submittedDate") # relevance | lastUpdatedDate | submittedDate
    sort_order: str = field(default="descending") # ascending | descending
    base_url: List[str] = field(default="https://export.arxiv.org/api/query") #API base URL
    http_timeout_seconds: int = field(default=10) # HTTP request timeout
    http_max_retries: int = field(default=3) # Max HTTP retries

class _PaperDataMetrics(BaseModel):
    total_requests: int = 0
    total_failures: int = 0
    total_entries: int = 0
    total_request_time_seconds: float = 0.0

    def record_request(self, duration_seconds: float, success: bool, entries: int) -> None:
        self.total_requests += 1
        self.total_request_time_seconds += duration_seconds
        if not success:
            self.total_failures += 1
        self.total_entries += entries

    @property
    def avg_request_latency(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_request_time_seconds / self.total_requests


class PaperDataFetcher:
    def __init__(self, settings: DataFetcherAttributes, metrics: Optional[_PaperDataMetrics] = None):
        self.settings = settings
        self.metrics = metrics or _PaperDataMetrics()

    def __parse_window(self, window: str) -> Tuple[str, str]:
        """
        Parse a window string like "1d", "2w", "3m" into
        (start_date, end_date) formatted as YYYYMMDD.
        """
        now = datetime.now(timezone.utc)
        if len(window) < 2:
            raise ValueError("Window must be like '1d', '2w', '3m' etc.")

        num_str, unit = window[:-1], window[-1].lower()
        if not num_str.isdigit():
            raise ValueError(f"Invalid window numeric part: {window}")

        value = int(num_str)

        if unit == "d":
            delta = timedelta(days=value)
        elif unit == "w":
            delta = timedelta(weeks=value)
        elif unit == "m":
            delta = relativedelta(months=value)
        else:
            raise ValueError(f"Invalid window unit '{unit}'. Use d, w, or m.")

        start = now - delta
        return start.strftime("%Y%m%d"), now.strftime("%Y%m%d")

    def __http_request(self, params: Dict[str, Any]) -> str:
        """
        Low-level HTTP request with retries and logging.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.settings.http_max_retries + 1):
            start_time = time.monotonic()
            try:
                logger.debug("arXiv request attempt %d with params=%s", attempt, params)
                resp = requests.get(
                    self.settings.base_url[0],
                    params=params,
                    timeout=self.settings.http_timeout_seconds,
                )
                duration = time.monotonic() - start_time

                if resp.status_code != 200:
                    logger.warning(
                        "Non-200 from arXiv (status=%s, attempt=%d)",
                        resp.status_code,
                        attempt,
                    )
                    self.metrics.record_request(duration, success=False, entries=0)
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                else:
                    self.metrics.record_request(duration, success=True, entries=0)
                    return resp.text

            except Exception as exc:
                duration = time.monotonic() - start_time
                self.metrics.record_request(duration, success=False, entries=0)
                last_exc = exc
                logger.warning(
                    "arXiv request failed (attempt=%d): %s",
                    attempt,
                    exc,
                    exc_info=True,
                )

            # simple backoff
            time.sleep(min(2 ** attempt, 5))

        raise RuntimeError(f"arXiv API request failed after {self.settings.http_max_retries} attempts") from last_exc

    def __fetch_entries(self) -> List[Any]:
        """
        Fetch all entries in the given window (relative time), paginating as needed.
        """
        window = self.settings.default_window
        sort_by = self.settings.sort_by
        sort_order = self.settings.sort_order

        start_date, end_date = self.__parse_window(window)
        query = f"cat:{self.settings.category} AND submittedDate:[{start_date} TO {end_date}]"

        logger.info(
            "Fetching arXiv entries window=%s (%s -> %s), category=%s, sort_by=%s, sort_order=%s",
            window,
            start_date,
            end_date,
            self.settings.category,
            sort_by,
            sort_order,
        )

        all_entries: List[Any] = []
        start_idx = 0

        while True:
            params = {
                "search_query": query,
                "start": start_idx,
                "max_results": self.settings.max_results,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            }

            xml = self.__http_request(params)
            feed = feedparser.parse(xml)

            entries = feed.entries or []
            num_entries = len(entries)
            self.metrics.total_entries += num_entries

            logger.info(
                "Fetched page start=%d, got %d entries",
                start_idx,
                num_entries,
            )

            if num_entries == 0:
                break

            all_entries.extend(entries)

            # returns fewer than requested on the last page
            if num_entries < self.settings.max_results:
                break

            start_idx += self.settings.max_results

        logger.info(
            "Finished fetching. Total entries=%d, total_requests=%d, failures=%d, avg_latency=%.3fs",
            len(all_entries),
            self.metrics.total_requests,
            self.metrics.total_failures,
            self.metrics.avg_request_latency,
        )
        return all_entries
    
    def __sanity_check_and_maniplulate(self, entries: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for e in entries:
            # Authors
            authors = []
            for a in getattr(e, "authors", []):
                name = getattr(a, "name", None)
                if name:
                    authors.append(name)

            # PDF link
            pdf_url = None
            for link in getattr(e, "links", []):
                link_type = getattr(link, "type", None)
                href = getattr(link, "href", None)
                if link_type == "application/pdf":
                    pdf_url = href
                    break

            primary_category = None
            pc = getattr(e, "arxiv_primary_category", None)
            if pc is not None:
                if isinstance(pc, dict):
                    primary_category = pc.get("term")
                else:
                    primary_category = getattr(pc, "term", None)

            # All categories / tags
            categories = []
            for t in getattr(e, "tags", []):
                # t may be dict-like or obj with .term
                if isinstance(t, dict):
                    term = t.get("term")
                else:
                    term = getattr(t, "term", None)
                if term:
                    categories.append(term)

            row = {
                # "id": getattr(e, "id", None),
                "title": getattr(e, "title", None),
                "summary": getattr(e, "summary", None),
                "authors": ", ".join(authors),
                "published": self._to_datetime(getattr(e, "published", None)),
                "updated": self._to_datetime(getattr(e, "updated", None)),
                "link": getattr(e, "link", None),
                "pdf_url": pdf_url,
                "primary_category": primary_category,
                "all_categories": ", ".join(categories),
                "doi": getattr(e, "arxiv_doi", None),
                "journal_ref": getattr(e, "arxiv_journal_ref", None),
                "comment": getattr(e, "arxiv_comment", None),
            }

            rows.append(row)

        return rows
    
    def _to_datetime(self,value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise TypeError(f"Invalid datetime value: {value!r}")
    
    async def run(self):
        paperDataEntries = self.__fetch_entries()

        if len(paperDataEntries) != 0:
            paperDataRows = self.__sanity_check_and_maniplulate(paperDataEntries)
            logger.info("Sanity Check and converting the data to insert into the db is completed")

            # asyncio.run(insert_rp_data(paperDataRows)) 
            await insert_rp_data(paperDataRows)
            logger.info(f"{len(paperDataRows)} rows has been inserted into the Database table : RPAbstractData")
        else:
            logger.error("0 row has been return by the API. skipping the data insert operation to the table RPAbstractData")



