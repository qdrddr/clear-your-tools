"""Fixtures for cache layout consolidation and injection latency regression tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import _default_at, load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.indexer.build import anthropic_tools_to_catalog_entries
from cyt.skills.catalog import build_registry, clear_registry_cache
from cyt.tools.catalog_cache import clear_decomposed_catalog_cache, ensure_tool_catalog_cached
from tests.support.permissions_gate_fixtures import patch_global_config_path
from tests.support.tiers_stats_fixtures import SKILLS_SOURCE_ROOT, patch_cyt_mcp_paths

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "cache_layout_consolidation"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class CacheLayoutScenario:
    id: str
    description: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class CacheLayoutPack:
    workspace: Path
    global_config_path: Path
    catalog_cache_dir: Path
    registry_snapshot_file: Path
    global_mcp_agg: Path
    global_mcp_defs: Path
    tools_cache_dir: Path
    skills_cache_dir: Path
    bm25_cache_dir: Path
    tools: list[dict[str, Any]]
    skill_fixture_name: str


def load_cache_layout_meta(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_cache_layout_scenarios(path: Path = SCENARIOS_PATH) -> tuple[CacheLayoutScenario, ...]:
    payload = load_cache_layout_meta(path)
    rows = payload.get("scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[CacheLayoutScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scenario_id = str(row.get("id") or "").strip()
        if not scenario_id:
            continue
        scenarios.append(
            CacheLayoutScenario(
                id=scenario_id,
                description=str(row.get("description") or ""),
                raw=dict(row),
            ),
        )
    return tuple(scenarios)


def load_cache_layout_scenario(
    scenario_id: str,
    path: Path = SCENARIOS_PATH,
) -> CacheLayoutScenario:
    for scenario in load_cache_layout_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"unknown cache layout scenario: {scenario_id!r}")


def expected_bundled_cache_paths() -> dict[str, str]:
    meta = load_cache_layout_meta()
    raw = meta.get("expected_cache_paths")
    if not isinstance(raw, dict):
        raise ValueError(f"{SCENARIOS_PATH}: expected_cache_paths required")
    return {str(key): str(value) for key, value in raw.items()}


def legacy_path_fragments() -> tuple[str, ...]:
    meta = load_cache_layout_meta()
    raw = meta.get("legacy_path_fragments")
    if not isinstance(raw, list):
        return ("/entries/",)
    return tuple(str(item) for item in raw)


def phase_timing_marker() -> str:
    meta = load_cache_layout_meta()
    return str(meta.get("phase_timing_marker") or "cyt: phase timing total=")


def materialize_cache_layout_workspace(tmp_path: Path) -> CacheLayoutPack:
    from tests.support.inject_preview_fixtures import GLOBAL_HOOK_CONFIG, materialize_fixture_pack

    inject_pack = materialize_fixture_pack(tmp_path)
    cyt_home = tmp_path / "cyt-home"
    tools_cache_dir = cyt_home / "cache" / "tools"
    skills_cache_dir = cyt_home / "cache" / "skills"
    bm25_cache_dir = cyt_home / "cache" / "bm25"
    for path in (tools_cache_dir, skills_cache_dir, bm25_cache_dir):
        path.mkdir(parents=True, exist_ok=True)

    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)
    global_config_path.write_text(
        GLOBAL_HOOK_CONFIG
        + (
            "\ncache:\n"
            f"  tools_dir: {tools_cache_dir}\n"
            f"  skills_dir: {skills_cache_dir}\n"
            f"  bm25_dir: {bm25_cache_dir}\n"
        ),
        encoding="utf-8",
    )

    meta = load_cache_layout_meta()
    registry_snapshot_file = tmp_path / "catalog-registry" / "registrations.json"

    return CacheLayoutPack(
        workspace=inject_pack.workspace,
        global_config_path=global_config_path,
        catalog_cache_dir=inject_pack.catalog_cache_dir,
        registry_snapshot_file=registry_snapshot_file,
        global_mcp_agg=inject_pack.global_mcp_agg,
        global_mcp_defs=inject_pack.global_mcp_defs,
        tools_cache_dir=tools_cache_dir,
        skills_cache_dir=skills_cache_dir,
        bm25_cache_dir=bm25_cache_dir,
        tools=list(inject_pack.tools),
        skill_fixture_name=str(meta.get("skill_fixture_name") or "create-hook.md"),
    )


def scoped_hook_config(pack: CacheLayoutPack) -> dict[str, Any]:
    config = load_config(pack.global_config_path)
    return set_hook_workspace_in_config(config, pack.workspace)


def patch_cache_layout_environment(
    monkeypatch: pytest.MonkeyPatch,
    pack: CacheLayoutPack,
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
    monkeypatch.setenv("HOME", str(pack.tools_cache_dir.parents[2]))


def assert_no_legacy_entries_layout(path: Path) -> None:
    text = path.as_posix()
    for fragment in legacy_path_fragments():
        assert fragment not in text, f"legacy cache fragment {fragment!r} in {path}"


def assert_flat_cache_root(root: Path) -> None:
    assert root.is_dir(), f"expected cache root directory: {root}"
    assert not (root / "entries").is_dir(), f"legacy entries/ dir must not exist under {root}"
    hash_dirs = [child for child in root.iterdir() if child.is_dir()]
    for child in hash_dirs:
        assert_no_legacy_entries_layout(child)


def _tools_with_layout_nonce(
    tools: list[dict[str, Any]],
    *,
    nonce: str,
) -> list[dict[str, Any]]:
    """Return catalog tools with a stable per-workspace nonce to avoid Rust hot-cache hits."""
    catalog_tools = [dict(tool) for tool in tools]
    if not catalog_tools:
        return catalog_tools
    first = dict(catalog_tools[0])
    description = str(first.get("description") or "").strip()
    first["description"] = f"{description} [{nonce}]".strip()
    catalog_tools[0] = first
    return catalog_tools


def seed_tool_disk_cache(
    pack: CacheLayoutPack,
    *,
    tools: list[dict[str, Any]] | None = None,
) -> Path:
    clear_decomposed_catalog_cache()
    config = scoped_hook_config(pack)
    catalog_tools = _tools_with_layout_nonce(
        list(tools if tools is not None else pack.tools),
        nonce=pack.tools_cache_dir.as_posix(),
    )
    entries, enums = anthropic_tools_to_catalog_entries(catalog_tools)
    result = ensure_tool_catalog_cached(entries, enums, config, bulk_id="cyt_mcp")
    assert result.catalog, "expected decomposed tool catalog payload"
    assert result.disk_backed, f"expected disk-backed tool cache, got {result.cache_status}"
    hash_dirs = [child for child in pack.tools_cache_dir.iterdir() if child.is_dir()]
    assert hash_dirs, "expected content-hash directories under tools cache root"
    assert_flat_cache_root(pack.tools_cache_dir)
    return pack.tools_cache_dir


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def seed_skill_disk_cache(pack: CacheLayoutPack) -> Path:
    clear_registry_cache()
    skills_dir = pack.workspace / ".agents" / "skills"
    source = SKILLS_SOURCE_ROOT / pack.skill_fixture_name
    if not source.is_file():
        raise FileNotFoundError(f"missing skill fixture: {source}")
    skill_name = pack.skill_fixture_name.replace(".md", "")
    target = skills_dir / skill_name / "SKILL.md"
    _write_skill(target, source.read_text(encoding="utf-8"))

    config = scoped_hook_config(pack)
    config = {
        **config,
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "directories": [str(skills_dir)],
            "pageindex": {"enable_bm25_chunking": True},
        },
        "agents": {
            "cursor": {"skills": {"directories": []}},
            "claude": {"skills": {"directories": []}},
            "codex": {"skills": {"directories": []}},
        },
    }
    entries = build_registry(config)
    assert entries, "expected at least one skill registry entry"
    entry_dir = Path(entries[0].entry_dir)
    assert entry_dir.is_dir()
    assert entry_dir.parent.resolve() == pack.skills_cache_dir.resolve()
    assert_no_legacy_entries_layout(entry_dir)
    assert_flat_cache_root(pack.skills_cache_dir)
    return entry_dir


def bundled_defaults_match_consolidated_layout() -> None:
    expected = expected_bundled_cache_paths()
    assert str(_default_at("cache", "tools_dir")) == expected["tools_dir"]
    assert str(_default_at("cache", "skills_dir")) == expected["skills_dir"]
    assert str(_default_at("cache", "bm25_dir")) == expected["bm25_dir"]
    assert str(_default_at("models", "bm25", "index_dir")) == expected["bm25_dir"]
    assert str(_default_at("tools", "pipelines", "bm25", "index_dir")) == expected["bm25_dir"]


@pytest.fixture
def isolated_cache_layout_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[CacheLayoutPack]:
    pack = materialize_cache_layout_workspace(tmp_path)
    patch_cache_layout_environment(monkeypatch, pack)
    clear_decomposed_catalog_cache()
    clear_registry_cache()
    yield pack
    clear_decomposed_catalog_cache()
    clear_registry_cache()


@pytest.fixture
def disk_catalog_cache_layout_pack(
    isolated_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
) -> CacheLayoutPack:
    from tests.support.inject_preview_fixtures import (
        patch_inject_preview_environment,
        seed_workspace_disk_catalog,
    )

    pack = isolated_cache_layout_pack
    patch_inject_preview_environment(monkeypatch, pack)
    seed_workspace_disk_catalog(pack)
    return pack


__all__ = [
    "CacheLayoutPack",
    "CacheLayoutScenario",
    "assert_flat_cache_root",
    "assert_no_legacy_entries_layout",
    "bundled_defaults_match_consolidated_layout",
    "disk_catalog_cache_layout_pack",
    "expected_bundled_cache_paths",
    "isolated_cache_layout_pack",
    "legacy_path_fragments",
    "load_cache_layout_meta",
    "load_cache_layout_scenario",
    "load_cache_layout_scenarios",
    "materialize_cache_layout_workspace",
    "patch_cache_layout_environment",
    "phase_timing_marker",
    "scoped_hook_config",
    "seed_skill_disk_cache",
    "seed_tool_disk_cache",
]
