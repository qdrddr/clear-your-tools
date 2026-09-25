"""Gherkin steps for empty cyt home cache bootstrap regression."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.tools.inject_cli import run_inject_preview
from cyt.tools.master_catalog import get_master_tool_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    register_ws_catalog,
    reset_catalog_state,
)
from tests.support.empty_home_cache_bootstrap_fixtures import (
    EmptyHomeFixturePack,
    clear_in_memory_hook_catalog_caches,
    load_bootstrap_fixture_meta,
    materialize_empty_home_workspace,
    patch_empty_home_environment,
    scoped_hook_config,
    simulate_cyt_mcp_push,
    simulate_daemon_warm,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "empty_home_cache_bootstrap.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _isolate_bootstrap_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_catalog_state()
    yield
    reset_catalog_state()


@given("an empty cyt home cache for the workspace")
def given_empty_home_cache(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.empty_home_cache_bootstrap_fixtures import assert_empty_cyt_cache

    pack = materialize_empty_home_workspace(tmp_path)
    patch_empty_home_environment(monkeypatch, pack)
    reset_catalog_state()
    pack.catalog_cache_dir.mkdir(parents=True, exist_ok=True)
    assert_empty_cyt_cache(pack)
    gherkin_context.payload["pack"] = pack


@given("a workspace cyt-mcp catalog is pushed to the hook daemon and disk")
def given_cyt_mcp_push(gherkin_context: GherkinContext) -> None:
    pack: EmptyHomeFixturePack = gherkin_context.payload["pack"]
    simulate_cyt_mcp_push(pack)


@given("in-memory hook catalog caches were cleared like a fresh CLI process")
def given_in_memory_cleared(gherkin_context: GherkinContext) -> None:
    clear_in_memory_hook_catalog_caches()


@when("cyt inject preview runs for the BM25 bootstrap prompt")
def when_inject_preview_runs(
    gherkin_context: GherkinContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack: EmptyHomeFixturePack = gherkin_context.payload["pack"]
    args = argparse.Namespace(
        query=pack.bm25_prompt,
        workspace=pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
        session=None,
    )
    gherkin_context.payload["preview_exit_code"] = run_inject_preview(args)
    captured = capsys.readouterr()
    gherkin_context.payload["preview_stdout"] = captured.out
    gherkin_context.payload["preview_stderr"] = captured.err


@when("daemon warm caches run for the workspace")
def when_daemon_warm_runs(gherkin_context: GherkinContext) -> None:
    pack: EmptyHomeFixturePack = gherkin_context.payload["pack"]
    simulate_daemon_warm(pack)
    config = scoped_hook_config(pack)
    gherkin_context.payload["master_catalog"] = get_master_tool_catalog(config, blocking=True)


@when("a workspace cyt-mcp catalog is registered with the hook daemon")
def when_catalog_registered(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack: EmptyHomeFixturePack = gherkin_context.payload["pack"]
    config = scoped_hook_config(pack)
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: config)
    register_ws_catalog(pack.workspace, pack.tools)
    gherkin_context.payload["master_catalog"] = get_master_tool_catalog(config, blocking=False)


@then("inject preview should fail with disk cache miss")
def then_preview_fails_disk_miss(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["preview_exit_code"] == 1
    err = str(gherkin_context.payload["preview_stderr"])
    assert "No tools in master hook catalog" in err
    assert "disk_cache=miss" in err


@then("inject preview should include expected cyt-mcp tool names")
def then_preview_includes_tools(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["preview_exit_code"] == 0
    out = str(gherkin_context.payload["preview_stdout"])
    pack: EmptyHomeFixturePack = gherkin_context.payload["pack"]
    injected_names = {
        name for name in pack.expected_tool_names if f"name='{name}'" in out or name in out
    }
    assert injected_names, "expected at least one BM25-relevant tool in injection output"
    assert injected_names.issubset(set(pack.expected_tool_names))


@then("inject preview stderr should not mention empty master hook catalog")
def then_preview_stderr_clean(gherkin_context: GherkinContext) -> None:
    err = str(gherkin_context.payload["preview_stderr"])
    assert "No tools in master hook catalog" not in err


@then("master catalog tool count should meet the bootstrap scenario minimum")
def then_master_catalog_minimum(gherkin_context: GherkinContext) -> None:
    meta = load_bootstrap_fixture_meta()
    minimum = int(meta.get("minimum_master_catalog_tools") or 0)
    catalog = gherkin_context.payload.get("master_catalog") or []
    assert len(catalog) >= minimum
