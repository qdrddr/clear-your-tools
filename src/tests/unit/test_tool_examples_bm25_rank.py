"""BM25-ranked tool example injection regression against catalog tools.

Input fixtures (``fixtures/tool_examples/input/``):

- ``captures.json`` — flat array of path→value captures for the SQLite store
  (``path``: ``<mcp_server>.<tool_name>.inputSchema.properties.*``)
- ``queries.json`` — five user queries and which catalog tools to enrich
- ``bm25_enrich_golden_<query_id>.json`` — expected enriched tools + flattened paths

Catalog tool schemas are loaded from ``fixtures/cyt_mcp_catalog/input/tools.json``.
Actual output is written to ``fixtures/tool_examples/out/`` on each test run.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.enrich import _collect_property_nodes, enrich_tools_with_examples
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.paths import FIXTURES_DIR

TOOL_EXAMPLES_DIR = FIXTURES_DIR / "tool_examples"
INPUT_DIR = TOOL_EXAMPLES_DIR / "input"
OUTPUT_DIR = TOOL_EXAMPLES_DIR / "out"
CAPTURES_FIXTURE = INPUT_DIR / "captures.json"
QUERIES_FIXTURE = INPUT_DIR / "queries.json"
CATALOG_FIXTURE = FIXTURES_DIR / "cyt_mcp_catalog" / "input" / "tools.json"

assert CAPTURES_FIXTURE.is_file(), f"missing fixture: {CAPTURES_FIXTURE}"
assert QUERIES_FIXTURE.is_file(), f"missing fixture: {QUERIES_FIXTURE}"
assert CATALOG_FIXTURE.is_file(), f"missing fixture: {CATALOG_FIXTURE}"


def _load_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _golden_path(query_id: str) -> Path:
    return INPUT_DIR / f"bm25_enrich_golden_{query_id}.json"


def _query_ids() -> tuple[str, ...]:
    queries_payload = _load_json(QUERIES_FIXTURE)
    queries = queries_payload.get("queries")
    assert isinstance(queries, list)
    ids = [str(item["id"]) for item in queries if isinstance(item, dict)]
    assert len(ids) == 5, f"expected 5 queries, got {len(ids)}"
    return tuple(ids)


QUERY_IDS = _query_ids()


def _catalog_tools_by_name() -> dict[str, dict[str, Any]]:
    catalog = _load_json(CATALOG_FIXTURE)
    tools = catalog.get("tools")
    assert isinstance(tools, list)
    out: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            out[str(tool["name"])] = tool
    return out


def _install_staggered_capture_clock(monkeypatch: pytest.MonkeyPatch, *, start_ms: int = 1_000_000) -> None:
    state = {"ms": start_ms}

    def fake_time() -> float:
        current_ms = state["ms"]
        state["ms"] += 1000
        return current_ms / 1000.0

    monkeypatch.setattr("cyt.tool_examples.store.time.time", fake_time)


def _extract_example_values(description: str) -> list[str]:
    marker = "Examples:"
    if marker not in description:
        return []
    tail = description.split(marker, 1)[1].strip()
    if tail.endswith("."):
        tail = tail[:-1].strip()
    values: list[str] = []
    index = 0
    while index < len(tail):
        if tail[index] != "'":
            index += 1
            continue
        end = index + 1
        while end < len(tail) and tail[end] != "'":
            end += 1
        values.append(tail[index + 1 : end])
        index = end + 1
        while index < len(tail) and tail[index] in ", ":
            index += 1
    return values


def _flattened_key(server_key: str, tool_name: str, json_path: str) -> str:
    """Wire key: mcp_server.tool_name.inputSchema.properties.field"""
    return f"{server_key}.{tool_name}.{json_path}"


def _parse_capture_path_key(full_key: str) -> tuple[str, str, str]:
    """Split ``server.tool.inputSchema.properties.field`` into identity + json_path."""
    marker = ".inputSchema."
    if marker not in full_key:
        raise ValueError(f"expected .inputSchema. in capture path key: {full_key}")
    head, json_path_part = full_key.split(marker, 1)
    server_key, bare_tool = head.split(".", 1)
    return server_key, bare_tool, f"inputSchema.{json_path_part}"


def _wire_name(server_key: str, bare_tool: str) -> str:
    return f"{server_key}_{bare_tool}"


def _set_nested_arg(target: dict[str, Any], segments: list[str], value: Any) -> None:
    if not segments:
        return
    key = segments[0]
    rest = segments[1:]
    if not rest:
        target[key] = value
        return
    if rest[0] == "items[]":
        rest = rest[1:]
        if rest and rest[0] == "properties":
            obj: dict[str, Any] = {}
            _set_nested_arg(obj, rest[1:], value)
            target[key] = [obj]
            return
        target[key] = [value]
        return
    nested = target.get(key)
    if not isinstance(nested, dict):
        nested = {}
        target[key] = nested
    _set_nested_arg(nested, rest, value)


def _args_from_json_paths(path_values: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for json_path, value in path_values.items():
        if not json_path.startswith("inputSchema.properties."):
            raise ValueError(f"unsupported json_path: {json_path}")
        tail = json_path[len("inputSchema.properties.") :]
        _set_nested_arg(args, tail.split("."), value)
    return args


def _flattened_examples_from_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tool in tools:
        server_key, bare_tool = resolve_mcp_server_and_tool(tool)
        schema = tool.get("input_schema") or tool.get("inputSchema") or {}
        if not isinstance(schema, dict):
            continue
        for json_path, spec in _collect_property_nodes(schema):
            if not isinstance(spec, dict):
                continue
            values = _extract_example_values(str(spec.get("description") or ""))
            if not values:
                continue
            rows.append(
                {
                    "path": _flattened_key(server_key, bare_tool, json_path),
                    "values": values,
                }
            )
    rows.sort(key=lambda row: str(row["path"]))
    return rows


def _normalize_enriched_tool(tool: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "server_key": tool.get("server_key"),
        "tool_name": tool.get("tool_name"),
        "input_schema": tool.get("input_schema") or tool.get("inputSchema"),
    }
    if tool.get("cyt_catalog_source") is not None:
        out["cyt_catalog_source"] = tool.get("cyt_catalog_source")
    return out


def _normalize_enriched_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_enriched_tool(tool) for tool in tools]
    normalized.sort(key=lambda item: str(item.get("name") or ""))
    return normalized


def _examples_config(db_path: Path, workspace: Path, inject: dict[str, Any]) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": inject,
                },
            },
        },
        workspace,
    )


def _seed_captures(
    *,
    db_path: Path,
    workspace: Path,
    catalog_by_name: dict[str, dict[str, Any]],
    captures_payload: dict[str, Any],
) -> None:
    captures = captures_payload.get("captures")
    assert isinstance(captures, list)
    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace))
        for entry in captures:
            assert isinstance(entry, dict)
            full_path = str(entry["path"])
            value = entry["value"]
            server_key, bare_tool, json_path = _parse_capture_path_key(full_path)
            wire_name = _wire_name(server_key, bare_tool)
            tool = catalog_by_name[wire_name]
            resolved_server, resolved_tool = resolve_mcp_server_and_tool(tool)
            assert (resolved_server, resolved_tool) == (server_key, bare_tool)
            schema = tool.get("input_schema") or tool.get("inputSchema") or {}
            args = _args_from_json_paths({json_path: value})
            assert isinstance(schema, dict)
            store.upsert_capture(project_id, server_key, bare_tool, schema, args)
    finally:
        store.close()


def _run_enrich_for_query(
    *,
    query_entry: dict[str, Any],
    inject: dict[str, Any],
    db_path: Path,
    workspace: Path,
    catalog_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tool_names = query_entry.get("tool_names")
    assert isinstance(tool_names, list)
    tools = [copy.deepcopy(catalog_by_name[str(name)]) for name in tool_names]
    config = _examples_config(db_path, workspace, inject)
    enriched = enrich_tools_with_examples(tools, str(query_entry["query"]), config)
    normalized = _normalize_enriched_tools(enriched)
    return {
        "query_id": str(query_entry["id"]),
        "query": str(query_entry["query"]),
        "tool_names": [str(name) for name in tool_names],
        "tools": normalized,
        "flattened_examples": _flattened_examples_from_tools(enriched),
        "inject": inject,
    }


def _write_output(query_id: str, payload: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"bm25_enrich_golden_{query_id}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


@pytest.fixture
def catalog_by_name() -> dict[str, dict[str, Any]]:
    return _catalog_tools_by_name()


@pytest.fixture
def captures_payload() -> dict[str, Any]:
    return _load_json(CAPTURES_FIXTURE)


@pytest.fixture
def queries_payload() -> dict[str, Any]:
    return _load_json(QUERIES_FIXTURE)


@pytest.fixture
def seeded_examples_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_by_name: dict[str, dict[str, Any]],
    captures_payload: dict[str, Any],
) -> tuple[Path, Path]:
    _install_staggered_capture_clock(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = tmp_path / "tool_examples.db"
    _seed_captures(
        db_path=db_path,
        workspace=workspace,
        catalog_by_name=catalog_by_name,
        captures_payload=captures_payload,
    )
    return db_path, workspace


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_tool_examples_bm25_enrich_matches_golden(
    query_id: str,
    seeded_examples_db: tuple[Path, Path],
    queries_payload: dict[str, Any],
) -> None:
    """Enriched catalog tools must match golden schemas, descriptions, and flattened paths."""
    db_path, workspace = seeded_examples_db
    inject_raw = queries_payload.get("inject")
    assert isinstance(inject_raw, dict)
    inject = {
        "max_per_property": int(inject_raw.get("max_per_property", 3)),
        "max_value_chars": int(inject_raw.get("max_value_chars", 120)),
        "full_call_examples": bool(inject_raw.get("full_call_examples", False)),
        "cross_schema_fallback": bool(inject_raw.get("cross_schema_fallback", False)),
    }
    queries = queries_payload.get("queries")
    assert isinstance(queries, list)
    query_entry = next(item for item in queries if isinstance(item, dict) and str(item["id"]) == query_id)

    golden_path = _golden_path(query_id)
    assert golden_path.is_file(), f"missing golden fixture: {golden_path}"
    golden = _load_json(golden_path)
    actual_payload = _run_enrich_for_query(
        query_entry=query_entry,
        inject=inject,
        db_path=db_path,
        workspace=workspace,
        catalog_by_name=_catalog_tools_by_name(),
    )
    _write_output(query_id, actual_payload)

    assert actual_payload["query"] == golden["query"]
    assert actual_payload["tool_names"] == golden["tool_names"]
    assert actual_payload["tools"] == golden["tools"]
    assert actual_payload["flattened_examples"] == golden["flattened_examples"]
