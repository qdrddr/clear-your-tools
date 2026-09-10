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


@dataclass(frozen=True)
class RankedExample:
    item: Any
    text: str
    bm25_score: float
    recency_bonus: float
    total_score: float
    timestamp_ms: int


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
    return [
        ScoredItem(item=row.item, score=row.total_score)
        for row in rank_by_query_detailed(query, items, recency_weight=recency_weight)
    ]


def rank_by_query_detailed(
    query: str,
    items: Sequence[tuple[Any, str, int]],
    *,
    recency_weight: float = 0.001,
) -> list[RankedExample]:
    """Rank items with BM25 + recency, exposing each component score."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        rows = sorted(items, key=lambda row: row[2], reverse=True)
        return [
            RankedExample(
                item=payload,
                text=text,
                bm25_score=0.0,
                recency_bonus=0.0,
                total_score=float(ts),
                timestamp_ms=ts,
            )
            for payload, text, ts in rows
        ]
    texts = [text for _payload, text, _ts in items]
    timestamps = [ts for _payload, _text, ts in items]
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    span = max(max_ts - min_ts, 1)
    avg_len = sum(len(_tokenize(text)) for text in texts) / max(len(texts), 1)
    scored: list[RankedExample] = []
    for payload, text, ts in items:
        doc_len = len(_tokenize(text))
        bm25 = _bm25_score(query_tokens, text, avg_len=avg_len, doc_len=doc_len)
        recency_bonus = recency_weight * (ts - min_ts) / span
        scored.append(
            RankedExample(
                item=payload,
                text=text,
                bm25_score=bm25,
                recency_bonus=recency_bonus,
                total_score=bm25 + recency_bonus,
                timestamp_ms=ts,
            ),
        )
    scored.sort(key=lambda row: row.total_score, reverse=True)
    return scored
