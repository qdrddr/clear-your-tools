"""BM25 pruning regression against the real cyt-mcp catalog fixture.

Uses ``fixtures/cyt_mcp_catalog/input/tools.json`` with tiering disabled and compares
output to pre-generated golden files under ``fixtures/cyt_mcp_catalog/input/``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cyt.config import bm25_prune_enums, bm25_score_tool, bm25_score_tool_enum
from cyt.indexer.tokens import count_json_tokens
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.manager import _managers
from cyt_core.types.prune import PruneResult
from tests.support.paths import FIXTURES_DIR

CATALOG_FIXTURE_DIR = FIXTURES_DIR / "cyt_mcp_catalog"
CATALOG_INPUT_DIR = CATALOG_FIXTURE_DIR / "input"
CATALOG_FIXTURE = CATALOG_INPUT_DIR / "tools.json"
CATALOG_OUTPUT_DIR = CATALOG_FIXTURE_DIR / "out"
GOLDEN_FIXTURE_NAMES = (
    "bm25_prune_golden.json",
    "bm25_prune_golden_code_review_graph.json",
    "bm25_prune_golden_codebase_memory_index.json",
    "bm25_prune_golden_context_mode_sandbox.json",
    "bm25_prune_golden_gitnexus_impact.json",
    "bm25_prune_golden_jcodemunch_search.json",
)
GOLDEN_FIXTURES = tuple(CATALOG_INPUT_DIR / name for name in GOLDEN_FIXTURE_NAMES)
assert len(GOLDEN_FIXTURES) == 6
for _golden_path in GOLDEN_FIXTURES:
    assert _golden_path.is_file(), f"missing golden fixture: {_golden_path}"


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def _load_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "cyt_catalog_source": tool.get("cyt_catalog_source"),
        "input_schema": tool.get("input_schema") or tool.get("inputSchema"),
    }
    if "server_key" in tool:
        out["server_key"] = tool.get("server_key")
    if "tool_name" in tool:
        out["tool_name"] = tool.get("tool_name")
    return out


def _normalize_pruned_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_tool(t) for t in tools]
    normalized.sort(key=lambda item: str(item.get("name") or ""))
    return normalized


def _build_output_payload(
    *,
    golden_fixture: Path,
    golden: dict[str, Any],
    result: PruneResult,
    pruned_tools: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "golden_fixture": golden_fixture.name,
        "pruned_tools": pruned_tools,
        "query": result.query,
        "status": result.status,
        "tools_in": result.tools_in,
        "tools_out": result.tools_out,
        "score_tool": bm25_score_tool(config),
        "score_tool_enum": bm25_score_tool_enum(config),
        "prune_enums": bm25_prune_enums(config),
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "tokens_saved": result.tokens_saved,
        "tokenizer": golden.get("tokenizer", "tiktoken_compact_json"),
        "optional_properties_in": result.tool_properties_count_in,
        "optional_properties_out": result.tool_properties_count_out,
        "decomposed": result.decomposed,
        "must_include_tools": golden.get("must_include_tools", []),
    }


def _write_prune_output(golden_fixture: Path, payload: dict[str, Any]) -> Path:
    CATALOG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CATALOG_OUTPUT_DIR / golden_fixture.name
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def _bm25_config(tmp_path: Path, golden: dict[str, Any]) -> dict[str, Any]:
    index_dir = str(tmp_path / "bm25")
    bm25_pipeline: dict[str, Any] = {
        "index_dir": index_dir,
        "score_tool": float(golden["score_tool"]),
        "score_tool_enum": float(golden["score_tool_enum"]),
        "prune_enums": bool(golden["prune_enums"]),
    }
    return {
        "tools": {
            "enabled": True,
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(tmp_path / "tier_state.db")},
            },
            "sequence": ["bm25"],
            "policy": {
                "system_tool": "prune_optional",
                "mcp_tool": "prune_all",
                "minimum_tools": 5,
                "per_tool": {},
            },
            "pipelines": {"bm25": bm25_pipeline},
        },
        "models": {
            "bm25": {
                "index_dir": index_dir,
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
    }


@pytest.mark.parametrize("golden_fixture", GOLDEN_FIXTURES, ids=lambda p: p.name)
def test_cyt_mcp_catalog_bm25_prune_matches_golden(
    golden_fixture: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tiering off: BM25 pruner output must match the checked-in golden fixture."""
    golden = _load_json(golden_fixture)
    catalog = _load_json(CATALOG_FIXTURE)
    tools = catalog.get("tools")
    assert isinstance(tools, list)
    assert len(tools) == golden["tools_in"]

    query = str(golden["query"])
    monkeypatch.setenv("HOME", str(tmp_path))

    config = _bm25_config(tmp_path, golden)
    result = filter_tools_for_query(
        copy.deepcopy(tools),
        query,
        ["bm25"],
        config=config,
        for_hook=True,
        catalog_bulk_id="cyt_mcp",
    )

    assert result.status == golden["status"]
    assert result.tools_in == golden["tools_in"]
    assert result.tools_out == golden["tools_out"]
    assert bm25_score_tool(config) == float(golden["score_tool"])
    assert bm25_score_tool_enum(config) == float(golden["score_tool_enum"])
    assert bm25_prune_enums(config) == golden["prune_enums"]
    assert result.tokens_in == golden["tokens_in"]
    assert result.tokens_out == golden["tokens_out"]
    assert result.tokens_saved == golden["tokens_saved"]
    assert result.tool_properties_count_in == golden["optional_properties_in"]
    assert result.tool_properties_count_out == golden["optional_properties_out"]
    assert result.decomposed == golden["decomposed"]
    assert golden["tokenizer"] == "tiktoken_compact_json"
    assert count_json_tokens(result.tools) == golden["tokens_out"]
    assert result.tools is not None

    actual = _normalize_pruned_tools(result.tools)
    _write_prune_output(
        golden_fixture,
        _build_output_payload(
            golden_fixture=golden_fixture,
            golden=golden,
            result=result,
            pruned_tools=actual,
            config=config,
        ),
    )

    expected = golden["pruned_tools"]
    assert actual == expected

    kept_names = {str(t.get("name")) for t in actual}
    for tool_name in golden.get("must_include_tools", []):
        assert str(tool_name) in kept_names
