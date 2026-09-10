"""Lightweight relevance ranking for tool example values."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoredItem:
    item: Any
    score: float


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_]+", text.lower()) if token]


def _bm25_score(query_tokens: list[str], document: str, *, avg_len: float, doc_len: int) -> float:
    if not query_tokens or not document:
        return 0.0
    k1 = 1.2
    b = 0.75
    doc_tokens = _tokenize(document)
    if not doc_tokens:
        return 0.0
    tf_map: dict[str, int] = {}
    for token in doc_tokens:
        tf_map[token] = tf_map.get(token, 0) + 1
    score = 0.0
    for token in query_tokens:
        tf = tf_map.get(token, 0)
        if tf == 0:
            continue
        idf = math.log(1 + 1.0)
        denom = tf + k1 * (1 - b + b * (doc_len / max(avg_len, 1.0)))
        score += idf * ((tf * (k1 + 1)) / denom)
    return score


def rank_by_query(
    query: str,
    items: Sequence[tuple[Any, str, int]],
    *,
    recency_weight: float = 0.001,
) -> list[ScoredItem]:
    """Rank items with (payload, text_for_bm25, timestamp_ms)."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return [
            ScoredItem(item=payload, score=float(ts))
            for payload, _text, ts in sorted(items, key=lambda row: row[2], reverse=True)
        ]
    texts = [text for _payload, text, _ts in items]
    avg_len = sum(len(_tokenize(text)) for text in texts) / max(len(texts), 1)
    scored: list[ScoredItem] = []
    for payload, text, ts in items:
        doc_len = len(_tokenize(text))
        bm25 = _bm25_score(query_tokens, text, avg_len=avg_len, doc_len=doc_len)
        scored.append(ScoredItem(item=payload, score=bm25 + ts * recency_weight))
    scored.sort(key=lambda row: row.score, reverse=True)
    return scored
