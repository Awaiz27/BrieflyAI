"""Simple title-based deduplication for ranked results."""

from __future__ import annotations

from typing import Any


def simple_dedup_by_title(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        t = (r.get("title") or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(r)
        if len(out) >= k:
            break
    return out
