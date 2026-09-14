"""Shared loaders and seed helpers for tool-examples regression fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.report import QueryScoreReport
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.paths import FIXTURES_DIR

TOOL_EXAMPLES_DIR = FIXTURES_DIR / "tool_examples"
INPUT_DIR = TOOL_EXAMPLES_DIR / "input"
OUTPUT_DIR = TOOL_EXAMPLES_DIR / "out"
CAPTURES_FIXTURE = INPUT_DIR / "captures.json"
QUERIES_FIXTURE = INPUT_DIR / "queries.json"
RANKING_CAPTURES_FIXTURE = INPUT_DIR / "ranking_captures.json"
RANKING_QUERIES_FIXTURE = INPUT_DIR / "ranking_queries.json"
RANKING_TOOLS_FIXTURE = INPUT_DIR / "ranking_tools.json"
CATALOG_FIXTURE = FIXTURES_DIR / "cyt_mcp_catalog" / "input" / "tools.json"


def load_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"expected JSON object in {path}")
    return loaded


def catalog_tools_by_name() -> dict[str, dict[str, Any]]:
    catalog = load_json(CATALOG_FIXTURE)
    tools = catalog.get("tools")
    if not isinstance(tools, list):
        raise TypeError("catalog tools must be a list")
    out: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            out[str(tool["name"])] = tool
    return out


def query_entry_by_id(query_id: str) -> dict[str, Any]:
    payload = load_json(QUERIES_FIXTURE)
    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise TypeError("queries fixture must contain a queries list")
    for item in queries:
        if isinstance(item, dict) and str(item.get("id")) == query_id:
            return item
    known = [str(item.get("id")) for item in queries if isinstance(item, dict)]
    raise KeyError(f"unknown query id {query_id!r}; known: {', '.join(known)}")


def inject_settings_from_queries_fixture(
    queries_fixture: Path | None = None,
) -> dict[str, Any]:
    payload = load_json(queries_fixture or QUERIES_FIXTURE)
    inject_raw = payload.get("inject")
    if not isinstance(inject_raw, dict):
        return {}
    inject: dict[str, Any] = {
        "max_per_property": int(inject_raw.get("max_per_property", 3)),
        "max_value_chars": int(inject_raw.get("max_value_chars", 120)),
        "full_call_examples": bool(inject_raw.get("full_call_examples", False)),
        "cross_schema_fallback": bool(inject_raw.get("cross_schema_fallback", False)),
    }
    ranking_raw = inject_raw.get("ranking")
    if isinstance(ranking_raw, dict):
        inject["ranking"] = dict(ranking_raw)
    return inject


def ranking_tools_by_name() -> dict[str, dict[str, Any]]:
    payload = load_json(RANKING_TOOLS_FIXTURE)
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise TypeError("ranking tools fixture must contain a tools list")
    out: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            out[str(tool["name"])] = tool
    return out


def ranking_query_ids() -> tuple[str, ...]:
    payload = load_json(RANKING_QUERIES_FIXTURE)
    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise TypeError("ranking queries fixture must contain a queries list")
    ids = [str(item["id"]) for item in queries if isinstance(item, dict)]
    if not ids:
        raise ValueError("ranking queries fixture has no query ids")
    return tuple(ids)


def ranking_query_entry_by_id(query_id: str) -> dict[str, Any]:
    payload = load_json(RANKING_QUERIES_FIXTURE)
    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise TypeError("ranking queries fixture must contain a queries list")
    for item in queries:
        if isinstance(item, dict) and str(item.get("id")) == query_id:
            return item
    known = [str(item.get("id")) for item in queries if isinstance(item, dict)]
    raise KeyError(f"unknown ranking query id {query_id!r}; known: {', '.join(known)}")


def tools_for_ranking_query(
    query_entry: dict[str, Any],
    *,
    catalog_by_name: dict[str, dict[str, Any]] | None = None,
    ranking_by_name: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    import copy

    tool_names = query_entry.get("tool_names")
    if not isinstance(tool_names, list):
        raise TypeError("query entry tool_names must be a list")
    source = str(query_entry.get("tools_source") or "catalog")
    if source == "ranking":
        tools_map = ranking_by_name or ranking_tools_by_name()
    else:
        tools_map = catalog_by_name or catalog_tools_by_name()
    return [copy.deepcopy(tools_map[str(name)]) for name in tool_names]


def examples_config(
    db_path: Path,
    workspace: Path,
    inject: dict[str, Any],
    *,
    sequence: list[str] | None = None,
) -> dict[str, Any]:
    tools_block: dict[str, Any] = {
        "examples": {
            "enabled": True,
            "database": {"path": str(db_path)},
            "inject": inject,
        },
    }
    if sequence is not None:
        tools_block["sequence"] = sequence
    return set_hook_workspace_in_config({"tools": tools_block}, workspace)


def parse_capture_path_key(full_key: str) -> tuple[str, str, str]:
    marker = ".inputSchema."
    if marker not in full_key:
        raise ValueError(f"expected .inputSchema. in capture path key: {full_key}")
    head, json_path_part = full_key.split(marker, 1)
    server_key, bare_tool = head.split(".", 1)
    return server_key, bare_tool, f"inputSchema.{json_path_part}"


def wire_name(server_key: str, bare_tool: str) -> str:
    return f"{server_key}_{bare_tool}"


def _set_nested_arg(target: dict[str, Any], segments: list[str], value: object) -> None:
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


def args_from_json_paths(path_values: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for json_path, value in path_values.items():
        if not json_path.startswith("inputSchema.properties."):
            raise ValueError(f"unsupported json_path: {json_path}")
        tail = json_path[len("inputSchema.properties.") :]
        _set_nested_arg(args, tail.split("."), value)
    return args


def _resolve_tool_for_capture_path(
    full_path: str,
    *,
    catalog_by_name: dict[str, dict[str, Any]],
    ranking_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    server_key, bare_tool, _json_path = parse_capture_path_key(full_path)
    wire = wire_name(server_key, bare_tool)
    if wire in ranking_by_name:
        return ranking_by_name[wire]
    if wire in catalog_by_name:
        return catalog_by_name[wire]
    raise KeyError(f"no tool definition for capture path {full_path!r}")


def seed_captures(
    *,
    db_path: Path,
    workspace: Path,
    catalog_by_name: dict[str, dict[str, Any]] | None = None,
    ranking_by_name: dict[str, dict[str, Any]] | None = None,
    captures_payload: dict[str, Any] | None = None,
    captures_fixture: Path | None = None,
) -> None:
    catalog = catalog_by_name or catalog_tools_by_name()
    ranking = ranking_by_name or ranking_tools_by_name()
    captures_doc = captures_payload or load_json(captures_fixture or CAPTURES_FIXTURE)
    captures = captures_doc.get("captures")
    if not isinstance(captures, list):
        raise TypeError("captures fixture must contain a captures list")
    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace))
        for entry in captures:
            if not isinstance(entry, dict):
                raise TypeError("each capture must be an object")
            full_path = str(entry["path"])
            value = entry["value"]
            repeat = int(entry.get("repeat", 1))
            server_key, bare_tool, json_path = parse_capture_path_key(full_path)
            tool = _resolve_tool_for_capture_path(
                full_path,
                catalog_by_name=catalog,
                ranking_by_name=ranking,
            )
            resolved_server, resolved_tool = resolve_mcp_server_and_tool(tool)
            if (resolved_server, resolved_tool) != (server_key, bare_tool):
                raise ValueError(f"tool identity mismatch for {full_path}")
            schema = tool.get("input_schema") or tool.get("inputSchema") or {}
            if not isinstance(schema, dict):
                raise TypeError(f"tool schema must be object for {full_path}")
            args = args_from_json_paths({json_path: value})
            for _ in range(max(repeat, 1)):
                store.upsert_capture(project_id, server_key, bare_tool, schema, args)
    finally:
        store.close()


def seed_ranking_captures(
    *,
    db_path: Path,
    workspace: Path,
    catalog_by_name: dict[str, dict[str, Any]] | None = None,
    ranking_by_name: dict[str, dict[str, Any]] | None = None,
) -> None:
    seed_captures(
        db_path=db_path,
        workspace=workspace,
        catalog_by_name=catalog_by_name,
        ranking_by_name=ranking_by_name,
        captures_fixture=RANKING_CAPTURES_FIXTURE,
    )


def flattened_examples_from_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tool in tools:
        examples = tool.get("cyt_injection_examples")
        if not isinstance(examples, list) or not examples:
            continue
        wire_name = str(tool.get("name") or "")
        rows.append(
            {
                "tool": wire_name,
                "examples": examples,
            },
        )
    rows.sort(key=lambda row: str(row["tool"]))
    return rows


def normalize_enriched_tool(tool: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "server_key": tool.get("server_key"),
        "tool_name": tool.get("tool_name"),
        "input_schema": tool.get("input_schema") or tool.get("inputSchema"),
    }
    if tool.get("cyt_catalog_source") is not None:
        out["cyt_catalog_source"] = tool.get("cyt_catalog_source")
    examples = tool.get("cyt_injection_examples")
    if isinstance(examples, list) and examples:
        out["cyt_injection_examples"] = examples
    return out


def normalize_enriched_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [normalize_enriched_tool(tool) for tool in tools]
    normalized.sort(key=lambda item: str(item.get("name") or ""))
    return normalized


def run_ranking_enrich_for_query(
    *,
    query_id: str,
    db_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    query_entry = ranking_query_entry_by_id(query_id)
    inject = inject_settings_from_queries_fixture(RANKING_QUERIES_FIXTURE)
    tools = tools_for_ranking_query(query_entry)
    config = examples_config(db_path, workspace, inject, sequence=["bm25"])
    enriched = enrich_tools_with_examples(tools, str(query_entry["query"]), config)
    return {
        "query_id": query_id,
        "query": str(query_entry["query"]),
        "tool_names": [str(name) for name in query_entry["tool_names"]],
        "tools": normalize_enriched_tools(enriched),
        "flattened_examples": flattened_examples_from_tools(enriched),
        "inject": inject,
    }


def write_ranking_output(query_id: str, payload: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"ranking_enrich_golden_{query_id}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def install_staggered_capture_clock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    start_ms: int = 1_000_000,
) -> None:
    state = {"ms": start_ms}

    def fake_time() -> float:
        current_ms = state["ms"]
        state["ms"] += 1000
        return current_ms / 1000.0

    monkeypatch.setattr("cyt.tool_examples.store.time.time", fake_time)


def run_ranking_query_score_report(
    query_id: str,
    *,
    workspace: Path,
    db_path: Path,
    seed: bool = True,
) -> QueryScoreReport:
    from cyt.tool_examples.report import build_query_score_report

    query_entry = ranking_query_entry_by_id(query_id)
    inject = inject_settings_from_queries_fixture(RANKING_QUERIES_FIXTURE)
    if seed:
        seed_ranking_captures(db_path=db_path, workspace=workspace)
    tools = tools_for_ranking_query(query_entry)
    config = examples_config(db_path, workspace, inject, sequence=["bm25"])
    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace))
        return build_query_score_report(
            query_id=query_id,
            query=str(query_entry["query"]),
            tools=tools,
            config=config,
            store=store,
            project_id=project_id,
        )
    finally:
        store.close()


def run_query_score_report(
    query_id: str,
    *,
    workspace: Path,
    db_path: Path,
) -> QueryScoreReport:
    import copy

    from cyt.tool_examples.report import build_query_score_report

    query_entry = query_entry_by_id(query_id)
    inject = inject_settings_from_queries_fixture()
    seed_captures(db_path=db_path, workspace=workspace)
    catalog = catalog_tools_by_name()
    tool_names = query_entry.get("tool_names")
    if not isinstance(tool_names, list):
        raise TypeError("query entry tool_names must be a list")
    tools = [copy.deepcopy(catalog[str(name)]) for name in tool_names]
    config = examples_config(db_path, workspace, inject)
    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace))
        return build_query_score_report(
            query_id=query_id,
            query=str(query_entry["query"]),
            tools=tools,
            config=config,
            store=store,
            project_id=project_id,
        )
    finally:
        store.close()
