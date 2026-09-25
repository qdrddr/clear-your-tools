"""Gherkin steps for cache layout consolidation regression."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.cache.cli import run_cache_clear
from cyt.hook import catalog_registry as registry_mod
from cyt.hook.catalog_registry import load_catalog_registry_from_disk
from cyt.tools.inject_cli import run_inject_preview
from cyt_core.types.prune import PruneResult
from tests.support.cache_layout_consolidation_fixtures import (
    CacheLayoutPack,
    assert_flat_cache_root,
    assert_no_legacy_entries_layout,
    phase_timing_marker,
    seed_skill_disk_cache,
    seed_tool_disk_cache,
)
from tests.support.inject_preview_fixtures import (
    load_inject_preview_scenario,
    patch_inject_preview_environment,
    seed_workspace_disk_catalog,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cache_layout_consolidation.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _isolate_cache_layout_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    yield


@given("an isolated cache layout workspace")
def given_isolated_cache_layout(
    gherkin_context: GherkinContext,
    isolated_cache_layout_pack: CacheLayoutPack,
) -> None:
    gherkin_context.payload["pack"] = isolated_cache_layout_pack


@given("a workspace with cyt-mcp disk catalog seeded")
def given_disk_catalog_seeded(
    gherkin_context: GherkinContext,
    isolated_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_cache_layout_pack
    patch_inject_preview_environment(monkeypatch, pack)
    seed_workspace_disk_catalog(pack)
    gherkin_context.payload["pack"] = pack


@given("an isolated cache layout workspace with seeded tool cache")
def given_seeded_tool_cache(
    gherkin_context: GherkinContext,
    isolated_cache_layout_pack: CacheLayoutPack,
) -> None:
    pack = isolated_cache_layout_pack
    seed_tool_disk_cache(pack)
    gherkin_context.payload["pack"] = pack


@given("a legacy registry snapshot without catalog layer")
def given_legacy_registry_snapshot(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_dir.mkdir()
    snapshot_file = snapshot_dir / "registrations.json"
    snapshot_file.write_text(
        json.dumps(
            [
                {
                    "agent": "cursor",
                    "scope": "global",
                    "workspace_root": None,
                    "tools": [{"name": "legacy_tool", "input_schema": {}}],
                    "content_hash": "deadbeef",
                    "instance_id": "pid:old",
                    "registered_at": 1.0,
                    "last_seen_at": 1.0,
                    "stale": False,
                },
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_mod, "REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(registry_mod, "REGISTRY_SNAPSHOT_FILE", snapshot_file)
    gherkin_context.payload["snapshot_file"] = snapshot_file


@when("a tool catalog is built to disk cache")
def when_tool_catalog_built(gherkin_context: GherkinContext) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    root = seed_tool_disk_cache(pack)
    gherkin_context.payload["tool_cache_root"] = root


@when("a skills registry is built to disk cache")
def when_skills_registry_built(gherkin_context: GherkinContext) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    entry_dir = seed_skill_disk_cache(pack)
    gherkin_context.payload["skill_entry_dir"] = entry_dir


@when("catalog registry loads from disk")
def when_registry_loads(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["loaded_count"] = load_catalog_registry_from_disk(mark_stale=True)


@when("cyt inject preview runs with verbose flag")
def when_inject_preview_verbose(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    scenario = load_inject_preview_scenario()
    monkeypatch.chdir(pack.workspace)

    def fake_coordinated(
        query: str,
        config: dict,
        *,
        payload: dict | None = None,
        skills_allowed: bool = True,
        tools_allowed: bool = True,
        **kwargs: object,
    ) -> tuple:
        result = PruneResult(
            tools=pack.tools[:1],
            status="ok",
            query=query,
            tools_in=len(pack.tools),
            mcp_tools_in=len(pack.tools),
            tools_out=1,
            error=None,
        )
        return (
            None,
            None,
            [],
            {"cyt_mcp": result},
            {"total_ms": 55, "phases": [{"name": "preview-prune", "elapsed_ms": 55}]},
        )

    monkeypatch.setattr(
        "cyt.pruning.hook_bridge.run_hook_coordinated_prune",
        fake_coordinated,
    )

    args = argparse.Namespace(
        query=scenario.query,
        workspace=pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
        session=None,
        verbose=True,
    )
    gherkin_context.payload["preview_exit_code"] = run_inject_preview(args)
    captured = capsys.readouterr()
    gherkin_context.payload["preview_stderr"] = captured.err


@when("cyt cache clear runs for tools")
def when_cache_clear_tools(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["cache_clear_code"] = run_cache_clear(
        argparse.Namespace(tools=True, skills=False, bm25=False, catalog=False, all=False),
    )


@then("tool cache entry should be under cache tools without entries subdir")
def then_tool_cache_flat(gherkin_context: GherkinContext) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    root: Path = gherkin_context.payload["tool_cache_root"]
    assert root.resolve() == pack.tools_cache_dir.resolve()
    assert_flat_cache_root(root)


@then("skills cache entry should be under cache skills without entries subdir")
def then_skills_cache_flat(gherkin_context: GherkinContext) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    entry_dir: Path = gherkin_context.payload["skill_entry_dir"]
    assert entry_dir.parent.resolve() == pack.skills_cache_dir.resolve()
    assert_no_legacy_entries_layout(entry_dir)


@then("registry snapshot file should contain no legacy entries")
def then_snapshot_compacted(gherkin_context: GherkinContext) -> None:
    snapshot_file: Path = gherkin_context.payload["snapshot_file"]
    assert snapshot_file.is_file()
    assert json.loads(snapshot_file.read_text(encoding="utf-8")) == []


@then("preview stderr should include phase timing total")
def then_preview_phase_timing(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["preview_exit_code"] == 0
    assert phase_timing_marker() in str(gherkin_context.payload["preview_stderr"])


@then("consolidated tools cache directory should be absent")
def then_tools_cache_cleared(gherkin_context: GherkinContext) -> None:
    pack: CacheLayoutPack = gherkin_context.payload["pack"]
    assert gherkin_context.payload["cache_clear_code"] == 0
    assert not pack.tools_cache_dir.exists()
