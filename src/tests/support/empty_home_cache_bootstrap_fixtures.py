"""Fixtures for empty ~/.config/cyt home cache bootstrap regression tests."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.cyt_mcp.catalog import apply_fetched_catalog, cyt_mcp_catalog_slug
from cyt.cyt_mcp.catalog_disk import read_disk_catalog
from cyt.hook.catalog_registry import list_catalog_registrations
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tools.master_catalog import clear_master_catalog_cache, rebuild_master_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    load_ws_tools_catalog,
    register_ws_catalog,
    reset_catalog_state,
)
from tests.support.inject_preview_fixtures import GLOBAL_HOOK_CONFIG, load_catalog_tools
from tests.support.permissions_gate_fixtures import patch_global_config_path
from tests.support.tiers_stats_fixtures import SKILLS_SOURCE_ROOT, patch_cyt_mcp_paths

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "empty_home_cache_bootstrap"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class EmptyHomeScenario:
    id: str
    description: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class EmptyHomeFixturePack:
    workspace: Path
    global_config_path: Path
    catalog_cache_dir: Path
    registry_snapshot_file: Path
    global_mcp_agg: Path
    global_mcp_defs: Path
    tools: list[dict[str, Any]]
    bm25_prompt: str
    expected_tool_names: tuple[str, ...]
    expected_injection_markers: tuple[str, ...]
    minimum_master_catalog_tools: int
    skill_fixture_name: str


def load_bootstrap_scenarios(path: Path = SCENARIOS_PATH) -> tuple[EmptyHomeScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[EmptyHomeScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scenario_id = str(row.get("id") or "").strip()
        if not scenario_id:
            continue
        scenarios.append(
            EmptyHomeScenario(
                id=scenario_id,
                description=str(row.get("description") or ""),
                raw=dict(row),
            ),
        )
    return tuple(scenarios)


def load_bootstrap_scenario(
    scenario_id: str,
    path: Path = SCENARIOS_PATH,
) -> EmptyHomeScenario:
    for scenario in load_bootstrap_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"unknown empty home bootstrap scenario: {scenario_id!r}")


def load_bootstrap_fixture_meta(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def compose_bm25_tools_catalog() -> list[dict[str, Any]]:
    """Workspace catalog: ws resilience tools plus fff_grep from inject preview fixtures."""
    ws_tools = load_ws_tools_catalog()
    _, inject_tools = load_catalog_tools()
    fff_tool = next(tool for tool in inject_tools if tool["name"] == "fff_grep")
    names = {tool["name"] for tool in ws_tools}
    if fff_tool["name"] not in names:
        return [*ws_tools, dict(fff_tool)]
    return list(ws_tools)


def materialize_empty_home_workspace(tmp_path: Path) -> EmptyHomeFixturePack:
    meta = load_bootstrap_fixture_meta()
    tools = compose_bm25_tools_catalog()

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
        '{"mcpServers": {"semble": {}, "gitnexus": {}, "fff": {}, "code-review-graph": {}}}',
        encoding="utf-8",
    )

    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True)
    global_config_path.write_text(GLOBAL_HOOK_CONFIG, encoding="utf-8")

    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir(exist_ok=True)
    registry_snapshot_file = tmp_path / "catalog-registry" / "registrations.json"
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    expected_raw = meta.get("expected_tool_names")
    markers_raw = meta.get("expected_injection_markers")
    if not isinstance(expected_raw, list) or not isinstance(markers_raw, list):
        raise ValueError(f"{SCENARIOS_PATH}: expected_tool_names and expected_injection_markers required")

    return EmptyHomeFixturePack(
        workspace=workspace,
        global_config_path=global_config_path,
        catalog_cache_dir=catalog_cache_dir,
        registry_snapshot_file=registry_snapshot_file,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
        tools=tools,
        bm25_prompt=str(meta.get("bm25_prompt") or ""),
        expected_tool_names=tuple(str(name) for name in expected_raw),
        expected_injection_markers=tuple(str(marker) for marker in markers_raw),
        minimum_master_catalog_tools=int(meta.get("minimum_master_catalog_tools") or len(tools)),
        skill_fixture_name=str(meta.get("skill_fixture_name") or "create-hook.md"),
    )


def _fast_registry_wait_hook_config(config: dict[str, Any]) -> dict[str, Any]:
    hook = config.setdefault("pruning", {}).setdefault("tools", {}).setdefault("hook", {})
    hook.setdefault("cyt_mcp", {}).setdefault("cache", {})["registry_wait_seconds"] = 0.05
    return config


def scoped_hook_config(pack: EmptyHomeFixturePack) -> dict[str, Any]:
    config = load_config(pack.global_config_path)
    return _fast_registry_wait_hook_config(set_hook_workspace_in_config(config, pack.workspace))


def skills_enabled_hook_config(
    pack: EmptyHomeFixturePack,
    *,
    skills_cache_dir: Path,
) -> dict[str, Any]:
    return _fast_registry_wait_hook_config(
        set_hook_workspace_in_config(
            {
                "cache": {"enabled": True, "skills_dir": str(skills_cache_dir)},
                "pruning": {
                    "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
                    "tools": {
                        "enabled": True,
                        "hook": {
                            "tools_from": ["cyt_mcp"],
                            "cyt_mcp": {"agent": "cursor"},
                        },
                    },
                },
                "skills": {
                    "enabled": True,
                    "directories": [str(pack.workspace / ".agents" / "skills")],
                },
            },
            pack.workspace,
        ),
    )


def assert_empty_cyt_cache(pack: EmptyHomeFixturePack) -> None:
    if pack.catalog_cache_dir.is_dir():
        assert not any(pack.catalog_cache_dir.iterdir()), "expected empty cyt-mcp catalog cache dir"
    if pack.registry_snapshot_file.is_file():
        payload = json.loads(pack.registry_snapshot_file.read_text(encoding="utf-8"))
        assert payload == [] or payload == {}, "expected empty registry snapshot"


def simulate_cyt_mcp_push(pack: EmptyHomeFixturePack, tools: list[dict[str, Any]] | None = None) -> None:
    """Mirror cyt-mcp push: registry snapshot + disk envelope + master rebuild."""
    catalog_tools = list(tools if tools is not None else pack.tools)
    register_ws_catalog(pack.workspace, catalog_tools)
    config = scoped_hook_config(pack)
    apply_fetched_catalog(config, catalog_tools)
    rebuild_master_catalog(config, blocking=True)


def clear_in_memory_hook_catalog_caches() -> None:
    """Drop in-memory catalog layers without wiping disk/registry (fresh CLI simulation)."""
    from cyt.cyt_mcp.catalog import _catalog_lock, _catalog_states

    clear_master_catalog_cache()
    with _catalog_lock:
        _catalog_states.clear()


def simulate_daemon_warm(pack: EmptyHomeFixturePack) -> None:
    """Clear in-memory caches then run the same warm path as hook daemon/proxy startup."""
    from cyt.cache import warm_caches

    clear_in_memory_hook_catalog_caches()
    warm_caches(scoped_hook_config(pack))


def simulate_proxy_registry_load() -> int:
    from cyt.hook.catalog_registry import load_catalog_registry_from_disk

    return load_catalog_registry_from_disk(mark_stale=True)


def seed_fixture_skill(pack: EmptyHomeFixturePack) -> Path:
    skills_dir = pack.workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    source = SKILLS_SOURCE_ROOT / pack.skill_fixture_name
    if not source.is_file():
        raise FileNotFoundError(f"missing skill fixture: {source}")
    skill_name = pack.skill_fixture_name.replace(".md", "")
    target_dir = skills_dir / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target_dir / "SKILL.md")
    return target_dir / "SKILL.md"


def patch_empty_home_environment(
    monkeypatch: pytest.MonkeyPatch,
    pack: EmptyHomeFixturePack,
) -> None:
    patch_cyt_mcp_paths(monkeypatch, pack)
    patch_global_config_path(monkeypatch, pack)
    monkeypatch.setattr(
        "cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_DIR",
        pack.registry_snapshot_file.parent,
    )
    monkeypatch.setattr(
        "cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_FILE",
        pack.registry_snapshot_file,
    )


def disk_cache_hit(pack: EmptyHomeFixturePack) -> bool:
    slug = cyt_mcp_catalog_slug(scoped_hook_config(pack))
    return read_disk_catalog(slug) is not None


def registry_has_workspace_registration(pack: EmptyHomeFixturePack) -> bool:
    workspace_text = str(pack.workspace.resolve())
    for row in list_catalog_registrations():
        if str(row.get("workspace_root") or "") == workspace_text:
            return True
    return False


@pytest.fixture
def isolated_empty_home_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[EmptyHomeFixturePack]:
    pack = materialize_empty_home_workspace(tmp_path)
    patch_empty_home_environment(monkeypatch, pack)
    reset_catalog_state()
    pack.catalog_cache_dir.mkdir(parents=True, exist_ok=True)
    assert_empty_cyt_cache(pack)
    yield pack
    reset_catalog_state()


__all__ = [
    "EmptyHomeFixturePack",
    "EmptyHomeScenario",
    "assert_empty_cyt_cache",
    "clear_in_memory_hook_catalog_caches",
    "compose_bm25_tools_catalog",
    "disk_cache_hit",
    "isolated_empty_home_pack",
    "load_bootstrap_fixture_meta",
    "load_bootstrap_scenario",
    "load_bootstrap_scenarios",
    "materialize_empty_home_workspace",
    "patch_empty_home_environment",
    "registry_has_workspace_registration",
    "scoped_hook_config",
    "seed_fixture_skill",
    "simulate_cyt_mcp_push",
    "simulate_daemon_warm",
    "simulate_proxy_registry_load",
    "skills_enabled_hook_config",
]
