"""Integration regression: tool_examples.db must never retain pytest/temp root_path rows."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tools.inject_cli import run_inject_preview
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
    disk_catalog_inject_preview_pack,
)
from tests.support.tool_examples_ephemeral_fixtures import (
    ToolExamplesGuardPack,
    ToolExamplesIntegrationScenario,
    count_ephemeral_tool_example_projects,
    load_integration_scenarios,
    load_polluted_seed,
    materialize_tool_examples_guard_pack,
    seed_polluted_tool_examples_db,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda s: s.id,
)
def test_tool_examples_ephemeral_guard_integration_scenarios(
    scenario: ToolExamplesIntegrationScenario,
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if scenario.id == "inject_preview_ephemeral_workspace":
        _assert_inject_preview_does_not_pollute_user_db(
            disk_catalog_inject_preview_pack,
            tmp_path,
            monkeypatch,
        )
    elif scenario.id == "record_and_enrich_ephemeral_workspace":
        _assert_record_and_enrich_do_not_pollute_user_db(tmp_path / "guard")
    elif scenario.id == "open_purges_legacy_pollution":
        _assert_open_purges_legacy_pollution(tmp_path / "guard")
    else:
        raise AssertionError(f"unknown integration scenario: {scenario.id}")


def _assert_inject_preview_does_not_pollute_user_db(
    pack: InjectPreviewFixturePack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = materialize_tool_examples_guard_pack(tmp_path / "guard")

    merged_global = guard.global_config_path.read_text(encoding="utf-8")
    merged_global += (
        f"\ntools:\n  examples:\n    enabled: true\n"
        f"    database:\n      path: {guard.user_examples_db_path}\n"
    )
    pack.global_config_path.write_text(merged_global, encoding="utf-8")

    monkeypatch.setattr(
        "cyt.permissions.merge.DEFAULT_USER_CONFIG_PATH",
        pack.global_config_path,
    )
    monkeypatch.setattr(
        "cyt.config.DEFAULT_USER_CONFIG_PATH",
        pack.global_config_path,
    )
    monkeypatch.chdir(pack.workspace)

    args = argparse.Namespace(
        query=pack.scenario.query,
        workspace=pack.workspace,
        json=True,
        definitions=False,
        source=["cyt_mcp"],
    )
    assert run_inject_preview(args) == 0
    assert count_ephemeral_tool_example_projects(guard.user_examples_db_path) == 0


def _assert_record_and_enrich_do_not_pollute_user_db(tmp_path: Path) -> None:
    from cyt.tool_examples.enrich import enrich_tools_with_examples
    from cyt.tool_examples.record import record_tool_examples_capture

    pack = materialize_tool_examples_guard_pack(tmp_path)
    schema = {"type": "object", "properties": {"search_query": {"type": "string"}}}

    assert (
        record_tool_examples_capture(
            workspace=pack.ephemeral_workspace,
            mcp_server="gitnexus",
            tool_name="query",
            input_schema=schema,
            args={"search_query": "x"},
            config=pack.config,
        )
        is None
    )
    tool = {
        "name": "gitnexus_query",
        "server_key": "gitnexus",
        "tool_name": "query",
        "input_schema": schema,
    }
    assert enrich_tools_with_examples([tool], "x", pack.config) == [tool]
    assert count_ephemeral_tool_example_projects(pack.user_examples_db_path) == 0


def _assert_open_purges_legacy_pollution(tmp_path: Path) -> None:
    from cyt.tool_examples.store import ToolExamplesStore

    pack = materialize_tool_examples_guard_pack(tmp_path)
    seed = load_polluted_seed()
    seed_polluted_tool_examples_db(
        db_path=pack.user_examples_db_path,
        real_project_root=pack.real_repo_root,
        seed=seed,
    )
    assert count_ephemeral_tool_example_projects(pack.user_examples_db_path) >= len(
        seed["ephemeral_projects"],
    )

    with patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True):
        ToolExamplesStore.open(str(pack.user_examples_db_path)).close()

    assert count_ephemeral_tool_example_projects(pack.user_examples_db_path) == 0
