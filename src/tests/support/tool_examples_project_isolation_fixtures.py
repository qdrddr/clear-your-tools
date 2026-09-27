"""Fixtures for cross-project tool example isolation regression tests."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.paths import FIXTURES_DIR

FIXTURES_ROOT = FIXTURES_DIR / "tool_examples_project_isolation"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class ProjectIsolationScenario:
    id: str
    description: str
    tool_key: str


@dataclass(frozen=True)
class IsolationToolSpec:
    wire_name: str
    mcp_server: str
    tool_name: str
    catalog_scope: str
    schema: dict[str, Any]
    repo_a_capture: dict[str, Any]
    repo_b_capture: dict[str, Any]
    enrich_query: str


@dataclass(frozen=True)
class TwoProjectPack:
    repo_a: Path
    repo_b: Path
    db_path: Path
    base_config: dict[str, Any]
    repo_a_slug: str
    repo_b_slug: str


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path}: expected object root")
    return payload


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[ProjectIsolationScenario, ...]:
    rows = load_scenarios(path).get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        ProjectIsolationScenario(
            id=str(row["id"]),
            description=str(row.get("description") or ""),
            tool_key=str(row["tool_key"]),
        )
        for row in rows
        if isinstance(row, dict)
    )


def tool_spec(tool_key: str, path: Path = SCENARIOS_PATH) -> IsolationToolSpec:
    tools = load_scenarios(path).get("tools")
    if not isinstance(tools, dict):
        raise ValueError(f"{path}: expected tools object")
    raw = tools.get(tool_key)
    if not isinstance(raw, dict):
        raise KeyError(f"unknown tool_key {tool_key!r}")
    schema = raw.get("schema")
    if not isinstance(schema, dict):
        raise TypeError(f"{tool_key}: schema must be an object")
    repo_a_capture = raw.get("repo_a_capture")
    repo_b_capture = raw.get("repo_b_capture")
    if not isinstance(repo_a_capture, dict) or not isinstance(repo_b_capture, dict):
        raise TypeError(f"{tool_key}: repo capture args must be objects")
    return IsolationToolSpec(
        wire_name=str(raw["wire_name"]),
        mcp_server=str(raw["mcp_server"]),
        tool_name=str(raw["tool_name"]),
        catalog_scope=str(raw.get("catalog_scope") or ""),
        schema=schema,
        repo_a_capture=dict(repo_a_capture),
        repo_b_capture=dict(repo_b_capture),
        enrich_query=str(raw.get("enrich_query") or ""),
    )


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def examples_config(db_path: Path, workspace: Path) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": {
                        "full_call_examples": True,
                        "max_full_call_examples": 3,
                        "ranking": {"diversity_threshold": 0.0},
                    },
                },
            },
        },
        workspace,
    )


def materialize_two_project_pack(tmp_path: Path) -> TwoProjectPack:
    scenarios = load_scenarios()
    projects = scenarios.get("projects")
    if not isinstance(projects, dict):
        raise ValueError("scenarios.json: expected projects object")
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    init_git_repo(repo_a)
    init_git_repo(repo_b)
    db_path = tmp_path / "tool_examples.db"
    base = examples_config(db_path, repo_a)
    repo_a_meta = projects.get("repo_a")
    repo_b_meta = projects.get("repo_b")
    if not isinstance(repo_a_meta, dict) or not isinstance(repo_b_meta, dict):
        raise TypeError("scenarios.json: project metadata must be objects")
    return TwoProjectPack(
        repo_a=repo_a,
        repo_b=repo_b,
        db_path=db_path,
        base_config=base,
        repo_a_slug=str(repo_a_meta.get("slug") or repo_a.name),
        repo_b_slug=str(repo_b_meta.get("slug") or repo_b.name),
    )


def tool_dict(spec: IsolationToolSpec) -> dict[str, Any]:
    return {
        "name": spec.wire_name,
        "server_key": spec.mcp_server,
        "tool_name": spec.tool_name,
        "input_schema": spec.schema,
        "cyt_catalog_scope": spec.catalog_scope,
    }


def foreign_project_slugs_in_examples(
    examples: list[dict[str, Any]] | None,
    *,
    own_slug: str,
    other_slug: str,
) -> list[str]:
    if not examples:
        return []
    text = json.dumps(examples, default=str).lower()
    found: list[str] = []
    if other_slug.lower() in text and other_slug.lower() != own_slug.lower():
        found.append(other_slug)
    return found


def record_capture(
    *,
    workspace: Path,
    spec: IsolationToolSpec,
    args: dict[str, Any],
    config: dict[str, Any],
) -> int | None:
    return record_tool_examples_capture(
        workspace=workspace,
        mcp_server=spec.mcp_server,
        tool_name=spec.tool_name,
        input_schema=spec.schema,
        args=args,
        config=config,
    )


def enrich_for_workspace(
    *,
    workspace: Path,
    spec: IsolationToolSpec,
    query: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    scoped = set_hook_workspace_in_config(config, workspace)
    enriched = enrich_tools_with_examples([tool_dict(spec)], query, scoped)
    return enriched[0].get("cyt_injection_examples") or []


def capture_count(db_path: Path, workspace: Path, spec: IsolationToolSpec) -> int:
    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace.resolve()))
        return len(store.list_captures(project_id, spec.mcp_server, spec.tool_name))
    finally:
        store.close()


def project_ids(db_path: Path, repo_a: Path, repo_b: Path) -> tuple[int, int]:
    store = ToolExamplesStore.open(str(db_path))
    try:
        return (
            store.get_or_create_project(str(repo_a.resolve())),
            store.get_or_create_project(str(repo_b.resolve())),
        )
    finally:
        store.close()
