"""Fixture loader for ``cyt inject preview`` workspace-scoped catalog regression tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.cyt_mcp.catalog import apply_fetched_catalog, clear_cyt_mcp_catalog_cache
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.injection.session_log_build import build_tool_log_entry
from cyt.tools.master_catalog import clear_master_catalog_cache, rebuild_master_catalog
from cyt_core.types.prune import PruneResult
from tests.support.permissions_gate_fixtures import patch_global_config_path
from tests.support.tiers_stats_fixtures import patch_cyt_mcp_paths

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "inject_preview"
CATALOG_TOOLS_PATH = FIXTURES_ROOT / "catalog_tools.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

GLOBAL_HOOK_CONFIG = """\
pruning:
  inject_via:
    cursor: hook
    claude: hook
    codex: hook
  tools:
    enabled: true
    hook:
      tools_from:
      - cyt_mcp
      cyt_mcp:
        agent: cursor
"""


@dataclass(frozen=True)
class InjectPreviewScenario:
    id: str
    description: str
    query: str
    expected_catalog_tool_count: int
    expected_pruned_tool_names: tuple[str, ...]
    expected_injection_markers: tuple[str, ...]


@dataclass(frozen=True)
class InjectPreviewFixturePack:
    workspace: Path
    global_config_path: Path
    catalog_cache_dir: Path
    global_mcp_agg: Path
    global_mcp_defs: Path
    tools: list[dict[str, Any]]
    scenario: InjectPreviewScenario


def load_catalog_tools(
    path: Path = CATALOG_TOOLS_PATH,
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names_raw = payload.get("catalog_tool_names")
    tools_raw = payload.get("tools")
    if not isinstance(names_raw, list):
        raise ValueError(f"{path}: expected catalog_tool_names array")
    if not isinstance(tools_raw, list):
        raise ValueError(f"{path}: expected tools array")
    names = tuple(str(name) for name in names_raw)
    tools = [dict(item) for item in tools_raw if isinstance(item, dict)]
    return names, tools


def load_inject_preview_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[InjectPreviewScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[InjectPreviewScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pruned_raw = row.get("expected_pruned_tool_names")
        markers_raw = row.get("expected_injection_markers")
        if not isinstance(pruned_raw, list) or not isinstance(markers_raw, list):
            raise ValueError(f"{path}: scenario {row.get('id')!r} missing expected arrays")
        scenarios.append(
            InjectPreviewScenario(
                id=str(row["id"]),
                description=str(row.get("description") or ""),
                query=str(row["query"]),
                expected_catalog_tool_count=int(row["expected_catalog_tool_count"]),
                expected_pruned_tool_names=tuple(str(name) for name in pruned_raw),
                expected_injection_markers=tuple(str(marker) for marker in markers_raw),
            ),
        )
    return tuple(scenarios)


def load_inject_preview_scenario(
    scenario_id: str = "workspace_scoped_disk_catalog",
    path: Path = SCENARIOS_PATH,
) -> InjectPreviewScenario:
    for scenario in load_inject_preview_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"unknown inject preview scenario: {scenario_id!r}")


def materialize_fixture_pack(tmp_path: Path) -> InjectPreviewFixturePack:
    _, tools = load_catalog_tools()
    scenario = load_inject_preview_scenario()

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    (cyt_config_dir / "config.yaml").write_text(
        "pruning:\n  tools:\n    enabled: true\n",
        encoding="utf-8",
    )
    (cyt_config_dir / "mcp-aggregator.yaml").write_text(
        "default_agent: cursor\n",
        encoding="utf-8",
    )
    (mcp_dir / "cursor.json").write_text(
        '{"mcpServers": {"semble": {}, "gitnexus": {}, "fff": {}}}',
        encoding="utf-8",
    )

    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True)
    global_config_path.write_text(GLOBAL_HOOK_CONFIG, encoding="utf-8")

    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir()
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    return InjectPreviewFixturePack(
        workspace=workspace,
        global_config_path=global_config_path,
        catalog_cache_dir=catalog_cache_dir,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
        tools=tools,
        scenario=scenario,
    )


def scoped_hook_config(pack: InjectPreviewFixturePack) -> dict[str, Any]:
    config = load_config(pack.global_config_path)
    return set_hook_workspace_in_config(config, pack.workspace)


def seed_workspace_disk_catalog(pack: InjectPreviewFixturePack) -> None:
    apply_fetched_catalog(scoped_hook_config(pack), pack.tools)
    rebuild_master_catalog(scoped_hook_config(pack), blocking=True)


def write_session_log(workspace: Path, session_id: str, entries: list[dict[str, Any]]) -> Path:
    log_dir = workspace / ".cursor" / "cyt" / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{session_id}.jsonl"
    lines = [json.dumps({"type": "meta", "agent": "cursor"})]
    lines.extend(json.dumps(entry) for entry in entries)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def tool_log_entry(
    tool: dict[str, Any],
    *,
    catalog_tools: list[dict[str, Any]],
    full: bool = True,
) -> dict[str, Any]:
    return build_tool_log_entry(
        tool,
        catalog="cyt_mcp",
        full=full,
        catalog_tools=catalog_tools,
    )


def patch_preview_prune_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return every catalog tool from preview prune so session gating is isolated."""
    from tests.support.injection_tier import stamp_tools_tier

    def _all_tools(
        tools: list[dict[str, Any]],
        query: str,
        **kwargs: object,
    ) -> PruneResult:
        count = len(tools)
        return PruneResult(
            tools=stamp_tools_tier(list(tools)),
            status="applied",
            query=query,
            tools_in=count,
            mcp_tools_in=count,
            tools_out=count,
            error=None,
        )

    monkeypatch.setattr("cyt.tools.inject_cli.filter_tools_for_query", _all_tools)


def json_payload_from_stdout(text: str) -> dict[str, Any]:
    """Parse JSON CLI output that may be prefixed by token budget lines."""
    start = text.find("{")
    if start < 0:
        raise ValueError("stdout did not contain JSON object")
    loaded = json.loads(text[start:])
    if not isinstance(loaded, dict):
        raise ValueError("stdout JSON root must be an object")
    return loaded


def patch_inject_preview_environment(
    monkeypatch: pytest.MonkeyPatch,
    pack: InjectPreviewFixturePack,
) -> None:
    patch_cyt_mcp_paths(monkeypatch, pack)
    patch_global_config_path(monkeypatch, pack)


@pytest.fixture
def inject_preview_pack(tmp_path: Path) -> InjectPreviewFixturePack:
    return materialize_fixture_pack(tmp_path)


@pytest.fixture
def disk_catalog_inject_preview_pack(
    inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[InjectPreviewFixturePack]:
    patch_inject_preview_environment(monkeypatch, inject_preview_pack)
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    seed_workspace_disk_catalog(inject_preview_pack)
    yield inject_preview_pack
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()


__all__ = [
    "InjectPreviewFixturePack",
    "InjectPreviewScenario",
    "disk_catalog_inject_preview_pack",
    "inject_preview_pack",
    "json_payload_from_stdout",
    "load_catalog_tools",
    "load_inject_preview_scenario",
    "load_inject_preview_scenarios",
    "materialize_fixture_pack",
    "patch_inject_preview_environment",
    "patch_preview_prune_all_tools",
    "scoped_hook_config",
    "seed_workspace_disk_catalog",
    "tool_log_entry",
    "write_session_log",
]
