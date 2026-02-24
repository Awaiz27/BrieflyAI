from typing import Any, Dict, List, Set


def simple_dedup_by_title(rows: List[Dict[str, Any]], k: int) -> List[Dict[str, Any]]:
    """
    Enterprise note:
    - Real systems dedup by semantic similarity + arXiv ids/versions
    - This simple rule prevents exact title repeats for MVP
    """
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for r in rows:
        t = (r.get("title") or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(r)
        if len(out) >= k:
            break

    return out
