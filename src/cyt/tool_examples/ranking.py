"""RRF ranking for tool example values with usage stats and diversity."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from cyt.config import pruning_pipeline_from_config
from cyt.pruners.bm25 import normalize_bm25_similarity
from cyt.pruners.llm import SELECTOR_SCORE_INSTRUCTION, llm_select_ids
from cyt.pruners.rerank import rerank_items, rerank_pruning_settings
from cyt.tiers.scores import wilson_lower_bound
from cyt.tool_examples.config import ExampleRankingConfig
from cyt.tool_examples.query import rank_by_query_detailed
from cyt.tool_examples.store import ToolExampleRow

logger = logging.getLogger(__name__)

_EXAMPLE_LLM_SYSTEM = (
    "You score example parameter values for relevance to a user query and property. "
    f"{SELECTOR_SCORE_INSTRUCTION}"
)


@dataclass(frozen=True)
class RankedExampleValue:
    value: str
    rrf_score: float
    usage_score: float
    bm25_score: float
    recency_bonus: float
    channel_ranks: dict[str, int] = field(default_factory=dict)


def usage_popularity_score(success_count: float, total_successes: float) -> float:
    return wilson_lower_bound(success_count, total_successes)


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, value in enumerate(ranking, start=1):
            scores[value] = scores.get(value, 0.0) + 1.0 / (k + rank)
    return scores


def _normalize_display_value(raw: str) -> str:
    text = raw
    if len(text) > 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    return text.strip().lower()


def _tokenize_value(text: str) -> set[str]:
    normalized = _normalize_display_value(text)
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if token}


def _bm25_pair_similarity(a: str, b: str) -> float:
    from cyt.tool_examples.query import _bm25_score, _tokenize

    tokens_a = _tokenize(_normalize_display_value(a))
    tokens_b = _tokenize(_normalize_display_value(b))
    if not tokens_a or not tokens_b:
        return 0.0
    avg_len = (len(tokens_a) + len(tokens_b)) / 2.0
    forward = _bm25_score(
        tokens_a,
        _normalize_display_value(b),
        avg_len=avg_len,
        doc_len=len(tokens_b),
    )
    backward = _bm25_score(
        tokens_b,
        _normalize_display_value(a),
        avg_len=avg_len,
        doc_len=len(tokens_a),
    )
    return max(normalize_bm25_similarity(forward), normalize_bm25_similarity(backward))


def _location_head(text: str) -> str | None:
    normalized = _normalize_display_value(text)
    if "," in normalized:
        head = normalized.split(",", 1)[0].strip()
        return head or None
    return normalized or None


def values_too_similar(a: str, b: str, *, threshold: float) -> bool:
    if a == b:
        return True
    norm_a = _normalize_display_value(a)
    norm_b = _normalize_display_value(b)
    if not norm_a or not norm_b:
        return norm_a == norm_b
    if norm_a in norm_b or norm_b in norm_a:
        return True
    head_a = _location_head(a)
    head_b = _location_head(b)
    if head_a and head_b and head_a == head_b:
        return True
    tokens_a = _tokenize_value(a)
    tokens_b = _tokenize_value(b)
    if tokens_a and tokens_b and (tokens_a <= tokens_b or tokens_b <= tokens_a):
        return True
    if tokens_a and tokens_b:
        union = tokens_a | tokens_b
        if union:
            jaccard = len(tokens_a & tokens_b) / len(union)
            if jaccard >= threshold:
                return True
    return _bm25_pair_similarity(a, b) >= threshold


def select_top_unique_values(
    scores: dict[str, float],
    *,
    max_count: int,
) -> list[str]:
    """Take highest-scoring values, deduping only exact string matches."""
    if max_count <= 0 or not scores:
        return []
    ordered = sorted(scores.keys(), key=lambda value: scores[value], reverse=True)
    selected: list[str] = []
    seen: set[str] = set()
    for value in ordered:
        if value in seen:
            continue
        selected.append(value)
        seen.add(value)
        if len(selected) >= max_count:
            break
    return selected


def select_diverse_values(
    scores: dict[str, float],
    *,
    max_count: int,
    diversity_threshold: float,
) -> list[str]:
    if max_count <= 0 or not scores:
        return []
    ordered = sorted(scores.keys(), key=lambda value: scores[value], reverse=True)
    selected: list[str] = []
    for value in ordered:
        if len(selected) >= max_count:
            break
        if any(
            values_too_similar(value, picked, threshold=diversity_threshold) for picked in selected
        ):
            continue
        selected.append(value)
    return selected


def _ranking_from_scores(scores: dict[str, float]) -> list[str]:
    return sorted(scores.keys(), key=lambda value: scores[value], reverse=True)


def _ranking_from_rows(
    rows: list[ToolExampleRow],
    *,
    key: str,
) -> list[str]:
    if key == "usage":
        total = float(sum(row.success_count for row in rows))
        scored = {
            row.value: usage_popularity_score(float(row.success_count), total) for row in rows
        }
    elif key == "recency":
        timestamps = [row.timestamp_ms for row in rows]
        min_ts = min(timestamps)
        max_ts = max(timestamps)
        span = max(max_ts - min_ts, 1)
        scored = {row.value: (row.timestamp_ms - min_ts) / span for row in rows}
    else:
        return []
    return _ranking_from_scores(scored)


def _bm25_ranking(query: str, rows: list[ToolExampleRow]) -> tuple[list[str], dict[str, float]]:
    items = [(row.value, row.value, row.timestamp_ms) for row in rows]
    ranked = rank_by_query_detailed(query, items)
    bm25_scores = {str(row.item): row.bm25_score for row in ranked}
    return [str(row.item) for row in ranked], bm25_scores


def _recency_ranking(rows: list[ToolExampleRow]) -> list[str]:
    return _ranking_from_rows(rows, key="recency")


def _usage_ranking(rows: list[ToolExampleRow]) -> tuple[list[str], dict[str, float]]:
    total = float(sum(row.success_count for row in rows))
    usage_scores = {
        row.value: usage_popularity_score(float(row.success_count), total) for row in rows
    }
    return _ranking_from_scores(usage_scores), usage_scores


def _property_label(json_path: str) -> str:
    marker = ".properties."
    if marker not in json_path:
        return json_path.rsplit(".", 1)[-1]
    return json_path.rsplit(".", 1)[-1]


def _rerank_ranking(
    query: str,
    rows: list[ToolExampleRow],
    *,
    property_name: str,
    property_description: str,
    config: dict[str, Any],
) -> list[str]:
    if not rows:
        return []
    context_query = query.strip()
    if property_description.strip():
        context_query = f"{context_query} {property_description}".strip()
    items = [
        {
            "value": row.value,
            "score": "0.0",
            "document": f"{property_name}: {_normalize_display_value(row.value)}",
        }
        for row in rows
    ]
    try:
        scored, _usage = rerank_items(
            context_query,
            items,
            rerank_pruning_settings(config),
            lambda item: str(item.get("document") or ""),
            None,
        )
    except Exception as exc:
        logger.warning("example rerank failed: %s", exc)
        return []
    return [str(item["value"]) for item in scored]


def _llm_ranking(
    query: str,
    rows: list[ToolExampleRow],
    *,
    property_name: str,
    property_description: str,
    config: dict[str, Any],
) -> list[str]:
    if not rows:
        return []
    context_query = query.strip()
    if property_description.strip():
        context_query = (
            f"{context_query}\nProperty: {property_name}\n{property_description}".strip()
        )
    formatted = [
        f"[{index}] {property_name}: {_normalize_display_value(row.value)}"
        for index, row in enumerate(rows)
    ]
    try:
        selected_scores, _usage = llm_select_ids(
            context_query,
            _EXAMPLE_LLM_SYSTEM,
            formatted,
            config=config,
            selector_kind="tools",
            phase_prefix="examples",
        )
    except Exception as exc:
        logger.warning("example llm ranking failed: %s", exc)
        return []
    scored_rows = [
        (row.value, float(selected_scores.get(index, 0))) for index, row in enumerate(rows)
    ]
    scored_rows.sort(key=lambda pair: pair[1], reverse=True)
    return [value for value, _score in scored_rows if _score > 0]


def effective_example_pipeline(
    config: dict[str, Any],
    ranking: ExampleRankingConfig,
    *,
    candidate_count: int,
) -> list[str]:
    if ranking.pipeline == "inherit":
        configured = pruning_pipeline_from_config(config)
    else:
        configured = list(ranking.pipeline)
    effective: list[str] = []
    for stage in configured:
        if stage == "bm25":
            effective.append("bm25")
        elif stage == "rerank" and candidate_count >= ranking.rerank_min_candidates:
            effective.append("rerank")
        elif stage == "llm" and candidate_count >= ranking.llm_min_candidates:
            effective.append("llm")
    if not effective:
        effective = ["bm25"]
    return effective


def rank_example_values(
    query: str,
    rows: list[ToolExampleRow],
    *,
    max_count: int,
    config: dict[str, Any],
    ranking: ExampleRankingConfig,
    property_name: str = "",
    property_description: str = "",
    exact_diversity: bool = False,
) -> list[RankedExampleValue]:
    if not rows or max_count <= 0:
        return []

    label = property_name or _property_label(rows[0].json_path)
    pipeline = effective_example_pipeline(
        config,
        ranking,
        candidate_count=len(rows),
    )

    rankings: list[list[str]] = []
    channel_ranks: dict[str, dict[str, int]] = {}
    bm25_scores: dict[str, float] = {}
    usage_scores: dict[str, float] = {}

    bm25_ranking, bm25_scores = _bm25_ranking(query, rows)
    rankings.append(bm25_ranking)
    channel_ranks["bm25"] = {value: idx + 1 for idx, value in enumerate(bm25_ranking)}

    usage_ranking, usage_scores = _usage_ranking(rows)
    rankings.append(usage_ranking)
    channel_ranks["usage"] = {value: idx + 1 for idx, value in enumerate(usage_ranking)}

    recency_ranking = _recency_ranking(rows)
    rankings.append(recency_ranking)
    channel_ranks["recency"] = {value: idx + 1 for idx, value in enumerate(recency_ranking)}

    if "rerank" in pipeline:
        rerank_ranking = _rerank_ranking(
            query,
            rows,
            property_name=label,
            property_description=property_description,
            config=config,
        )
        if rerank_ranking:
            rankings.append(rerank_ranking)
            channel_ranks["rerank"] = {value: idx + 1 for idx, value in enumerate(rerank_ranking)}

    if "llm" in pipeline:
        llm_ranking = _llm_ranking(
            query,
            rows,
            property_name=label,
            property_description=property_description,
            config=config,
        )
        if llm_ranking:
            rankings.append(llm_ranking)
            channel_ranks["llm"] = {value: idx + 1 for idx, value in enumerate(llm_ranking)}

    rrf_scores = reciprocal_rank_fusion(rankings, k=ranking.rrf_k)
    if exact_diversity:
        selected_values = select_top_unique_values(rrf_scores, max_count=max_count)
    else:
        selected_values = select_diverse_values(
            rrf_scores,
            max_count=max_count,
            diversity_threshold=ranking.diversity_threshold,
        )

    recency_by_value = {row.value: row.timestamp_ms for row in rows}
    min_ts = min(recency_by_value.values())
    max_ts = max(recency_by_value.values())
    span = max(max_ts - min_ts, 1)

    out: list[RankedExampleValue] = []
    for value in selected_values:
        ts = recency_by_value.get(value, min_ts)
        out.append(
            RankedExampleValue(
                value=value,
                rrf_score=rrf_scores.get(value, 0.0),
                usage_score=usage_scores.get(value, 0.0),
                bm25_score=bm25_scores.get(value, 0.0),
                recency_bonus=(ts - min_ts) / span,
                channel_ranks={
                    channel: ranks[value]
                    for channel, ranks in channel_ranks.items()
                    if value in ranks
                },
            ),
        )
    return out


def rank_full_call_examples(
    query: str,
    captures: list[tuple[dict[str, Any], int]],
    *,
    max_count: int,
    config: dict[str, Any],
    ranking: ExampleRankingConfig,
) -> list[dict[str, Any]]:
    """Rank and diversify full-call invocation payloads."""
    if not captures or max_count <= 0:
        return []
    payload_by_value: dict[str, dict[str, Any]] = {}
    rows: list[ToolExampleRow] = []
    for payload, ts in captures:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        payload_by_value.setdefault(serialized, payload)
        rows.append(
            ToolExampleRow(
                schema_id=0,
                json_path="full_call",
                value=serialized,
                value_type="object",
                timestamp_ms=ts,
                success_count=1,
            ),
        )
    ranked = rank_example_values(
        query,
        rows,
        max_count=max_count,
        config=config,
        ranking=ranking,
        property_name="full_call",
        property_description="Complete tool invocation arguments",
        exact_diversity=True,
    )
    out: list[dict[str, Any]] = []
    for item in ranked:
        payload = payload_by_value.get(item.value)
        if isinstance(payload, dict):
            out.append(payload)
    return out
