"""Smoke tests for tokens, BM25 search, and cohesion chunking."""

from __future__ import annotations

import json
from pathlib import Path

from cyt_indexer import bm25_cohesion_chunk, bm25_score_catalog, count_tokens
from cyt_indexer.bm25_cohesion import Bm25CohesionConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_count_tokens_smoke() -> None:
    assert count_tokens("hello world") >= 1


def test_bm25_score_catalog_smoke() -> None:
    catalog = json.loads((FIXTURES / "bm25_catalog.json").read_text(encoding="utf-8"))
    scored = bm25_score_catalog(catalog, "read files disk", prune_enums=False)
    scores = [float(item["score"]) for item in scored["json"]]
    assert scores[0] > scores[1]


def test_bm25_cohesion_chunk_smoke() -> None:
    sample = (FIXTURES / "cohesion_sample.md").read_text(encoding="utf-8")
    cfg = json.loads((FIXTURES / "cohesion_config.json").read_text(encoding="utf-8"))
    config = Bm25CohesionConfig(**{k: v for k, v in cfg.items() if k in Bm25CohesionConfig.__dataclass_fields__})
    chunks = bm25_cohesion_chunk(sample, config)
    assert chunks
    recompiled = "".join(str(chunk.get("text", "")) for chunk in chunks)
    assert recompiled == sample
