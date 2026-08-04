"""BM25 fuzzy tool-name resolution via cyt-indexer-sdk (no cyt.* imports)."""

from __future__ import annotations

from difflib import SequenceMatcher

from cyt_indexer.bm25_search import bm25_score_catalog

_FUZZY_THRESHOLD = 0.98


def _match_ratio(left: str, right: str) -> float:
    return float(SequenceMatcher(None, left, right).ratio())


def fuzzy_resolve_tool_name(names: list[str], query: str) -> str | None:
    """Return the highest BM25-ranked tool name when match ratio is at or above threshold."""
    candidates = [name for name in names if name]
    needle = str(query or "").strip()
    if not candidates or not needle:
        return None
    if needle in candidates:
        return needle

    wrapper: dict[str, list[dict[str, str]]] = {
        "json": [{"file_path": name, "content": name} for name in candidates],
        "md": [],
    }
    scored = bm25_score_catalog(wrapper, needle, prune_enums=False)
    items = scored.get("json") or []
    if not items:
        return None

    best_score = -1.0
    best_name: str | None = None
    for item in items:
        score = float(item.get("score", 0))
        if score > best_score:
            best_score = score
            best_name = str(item.get("file_path") or "")

    if not best_name:
        return None
    if _match_ratio(needle, best_name) >= _FUZZY_THRESHOLD:
        return best_name
    return None
