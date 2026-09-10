"""Shared loaders and seed helpers for tool-examples regression fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.report import QueryScoreReport
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.paths import FIXTURES_DIR

TOOL_EXAMPLES_DIR = FIXTURES_DIR / "tool_examples"
INPUT_DIR = TOOL_EXAMPLES_DIR / "input"
CAPTURES_FIXTURE = INPUT_DIR / "captures.json"
QUERIES_FIXTURE = INPUT_DIR / "queries.json"
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


def inject_settings_from_queries_fixture() -> dict[str, Any]:
    payload = load_json(QUERIES_FIXTURE)
    inject_raw = payload.get("inject")
    if not isinstance(inject_raw, dict):
        return {}
    return {
        "max_per_property": int(inject_raw.get("max_per_property", 3)),
        "max_value_chars": int(inject_raw.get("max_value_chars", 120)),
        "full_call_examples": bool(inject_raw.get("full_call_examples", False)),
        "cross_schema_fallback": bool(inject_raw.get("cross_schema_fallback", False)),
    }


def examples_config(db_path: Path, workspace: Path, inject: dict[str, Any]) -> dict[str, Any]:
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


def seed_captures(
    *,
    db_path: Path,
    workspace: Path,
    catalog_by_name: dict[str, dict[str, Any]] | None = None,
    captures_payload: dict[str, Any] | None = None,
) -> None:
    catalog = catalog_by_name or catalog_tools_by_name()
    captures_doc = captures_payload or load_json(CAPTURES_FIXTURE)
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
            server_key, bare_tool, json_path = parse_capture_path_key(full_path)
            tool = catalog[wire_name(server_key, bare_tool)]
            resolved_server, resolved_tool = resolve_mcp_server_and_tool(tool)
            if (resolved_server, resolved_tool) != (server_key, bare_tool):
                raise ValueError(f"catalog identity mismatch for {full_path}")
            schema = tool.get("input_schema") or tool.get("inputSchema") or {}
            if not isinstance(schema, dict):
                raise TypeError(f"tool schema must be object for {full_path}")
            args = args_from_json_paths({json_path: value})
            store.upsert_capture(project_id, server_key, bare_tool, schema, args)
    finally:
        store.close()


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
