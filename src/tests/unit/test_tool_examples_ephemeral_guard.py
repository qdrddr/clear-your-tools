"""Regression tests: pytest/temp paths must not pollute tool_examples.db."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.common.paths import is_ephemeral_workspace_path
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.tool_examples_ephemeral_fixtures import (
    ToolExamplesGuardPack,
    count_ephemeral_tool_example_projects,
    load_polluted_seed,
    load_scenarios,
    seed_polluted_tool_examples_db,
    tool_examples_guard_pack,
)

_SCHEMA = {"type": "object", "properties": {"search_query": {"type": "string"}}}
_EXAMPLE_CONFIG = {
    "tools": {
        "examples": {
            "enabled": True,
            "database": {"path": "/tmp/user_tool_examples.db"},
        },
    },
}


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_workspace_paths"],
)
def test_fixture_ephemeral_workspaces_are_ephemeral(path: str) -> None:
    assert is_ephemeral_workspace_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["persistent_workspace_paths"],
)
def test_fixture_persistent_workspaces_are_not_ephemeral(path: str) -> None:
    assert not is_ephemeral_workspace_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_workspace_paths"],
)
def test_fixture_ephemeral_workspaces_skip_tool_example_record(path: str) -> None:
    assert (
        record_tool_examples_capture(
            workspace=Path(path),
            mcp_server="gitnexus",
            tool_name="query",
            input_schema=_SCHEMA,
            args={"search_query": "auth flow"},
            config=_EXAMPLE_CONFIG,
        )
        is None
    )


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_workspace_paths"],
)
def test_fixture_ephemeral_workspaces_skip_tool_example_enrich(path: str) -> None:
    tool = {
        "name": "gitnexus_query",
        "server_key": "gitnexus",
        "tool_name": "query",
        "input_schema": _SCHEMA,
    }
    config = {
        **_EXAMPLE_CONFIG,
        "_cyt_hook_workspace_root": path,
    }
    assert enrich_tools_with_examples([tool], "BM25 ranking", config) == [tool]


def test_open_purges_polluted_seed_on_user_db(
    tool_examples_guard_pack: ToolExamplesGuardPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_tool_examples_db(
        db_path=tool_examples_guard_pack.user_examples_db_path,
        real_project_root=tool_examples_guard_pack.real_repo_root,
        seed=seed,
    )
    before = count_ephemeral_tool_example_projects(
        tool_examples_guard_pack.user_examples_db_path,
    )
    assert before >= len(seed["ephemeral_projects"])

    with patch(
        "cyt.tool_examples.store.is_default_user_cyt_db",
        return_value=True,
    ):
        ToolExamplesStore.open(str(tool_examples_guard_pack.user_examples_db_path)).close()

    assert (
        count_ephemeral_tool_example_projects(tool_examples_guard_pack.user_examples_db_path)
        == 0
    )


def test_open_does_not_purge_isolated_test_db(
    tool_examples_guard_pack: ToolExamplesGuardPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_tool_examples_db(
        db_path=tool_examples_guard_pack.user_examples_db_path,
        real_project_root=tool_examples_guard_pack.real_repo_root,
        seed=seed,
    )
    ToolExamplesStore.open(str(tool_examples_guard_pack.user_examples_db_path)).close()
    assert (
        count_ephemeral_tool_example_projects(tool_examples_guard_pack.user_examples_db_path)
        >= len(seed["ephemeral_projects"])
    )


def test_enrich_skips_ephemeral_workspace_without_touching_user_db(
    tool_examples_guard_pack: ToolExamplesGuardPack,
) -> None:
    tool = {
        "name": "gitnexus_query",
        "server_key": "gitnexus",
        "tool_name": "query",
        "input_schema": _SCHEMA,
    }
    enriched = enrich_tools_with_examples(
        [tool],
        "BM25 ranking",
        tool_examples_guard_pack.config,
    )
    assert enriched == [tool]
    assert (
        count_ephemeral_tool_example_projects(tool_examples_guard_pack.user_examples_db_path)
        == 0
    )
