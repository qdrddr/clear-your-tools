"""Manual BM25 smoke against shared e2e fixture (not default CI).

Run:
  uv run pytest src/tests/qa -m qa --run-qa -v
  ./scripts/local/tests/pytest-category.sh qa -- "read files from disk"
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from cyt.pruners.bm25 import BM25_SCORE, bm25_catalog_dict
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.qa

DEFAULT_QUERY = "read files from disk"
FIXTURE_PATH = REPO_ROOT / "sdk" / "e2e" / "fixtures" / "bm25_catalog.json"


def _score_value(entry: dict[str, Any]) -> float:
    raw = entry.get("score", 0)
    if isinstance(raw, str):
        return float(raw)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def _bm25_config(tmp_path: Path) -> dict[str, Any]:
    index_dir = str(tmp_path / "bm25")
    return {
        "models": {
            "bm25": {
                "index_dir": index_dir,
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
        "pruning": {
            "tools": {
                "sequence": ["bm25"],
                "policy": {"per_tool": {}, "minimum_tools": 1},
                "pipelines": {"bm25": {"index_dir": index_dir}},
            },
        },
    }


@pytest.fixture
def bm25_catalog() -> dict[str, Any]:
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    loaded: Any = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def test_bm25_fixture_ranks_read_above_write(
    bm25_catalog: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared e2e catalog: disk-read query should outrank unrelated write chunk."""
    monkeypatch.setenv("HOME", str(tmp_path))
    scored, _usage = bm25_catalog_dict(
        copy.deepcopy(bm25_catalog),
        DEFAULT_QUERY,
        prune=False,
        config=_bm25_config(tmp_path),
    )
    by_path = {
        str(entry.get("file_path", "")): _score_value(entry)
        for entry in scored.get("json") or []
        if isinstance(entry, dict)
    }
    read_score = by_path["schemas/decomposed/mcp__test__read.json"]
    write_score = by_path["schemas/decomposed/mcp__test__write.json"]
    assert read_score > write_score


def test_bm25_fixture_prune_keeps_read_tool(
    bm25_catalog: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prune pass should retain read-related tool above BM25_SCORE."""
    query = DEFAULT_QUERY
    monkeypatch.setenv("HOME", str(tmp_path))
    scored, _usage = bm25_catalog_dict(
        copy.deepcopy(bm25_catalog),
        query,
        prune=True,
        config=_bm25_config(tmp_path),
    )
    kept = scored.get("json") or []
    assert kept, f"expected tools above threshold {BM25_SCORE} for query {query!r}"
    assert any("read" in str(item.get("file_path", "")).lower() for item in kept)
