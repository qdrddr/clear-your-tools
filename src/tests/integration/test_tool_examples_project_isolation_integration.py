"""Integration tests: shared tool_examples.db stays isolated per git project."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.tool_examples_project_isolation_fixtures import (
    ProjectIsolationScenario,
    capture_count,
    enrich_for_workspace,
    foreign_project_slugs_in_examples,
    load_integration_scenarios,
    materialize_two_project_pack,
    record_capture,
    tool_spec,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda s: s.id,
)
def test_tool_examples_project_isolation_integration_scenarios(
    scenario: ProjectIsolationScenario,
    tmp_path: Path,
) -> None:
    _assert_record_enrich_isolated(tmp_path, scenario.tool_key)


def _assert_record_enrich_isolated(tmp_path: Path, tool_key: str) -> None:
    pack = materialize_two_project_pack(tmp_path)
    spec = tool_spec(tool_key)

    record_capture(
        workspace=pack.repo_a,
        spec=spec,
        args=spec.repo_a_capture,
        config=pack.base_config,
    )

    assert capture_count(pack.db_path, pack.repo_a, spec) >= 1
    assert capture_count(pack.db_path, pack.repo_b, spec) == 0

    examples_b = enrich_for_workspace(
        workspace=pack.repo_b,
        spec=spec,
        query=spec.enrich_query,
        config=pack.base_config,
    )
    assert examples_b == []
    assert (
        foreign_project_slugs_in_examples(
            examples_b,
            own_slug=pack.repo_b_slug,
            other_slug=pack.repo_a_slug,
        )
        == []
    )


@pytest.mark.asyncio
async def test_hook_record_stores_capture_under_request_workspace_only(
    tmp_path: Path,
) -> None:
    tool_key = "workspace_scoped"
    from cyt.hook.http_server import hook_tool_examples_record

    pack = materialize_two_project_pack(tmp_path)
    spec = tool_spec(tool_key)
    config = set_hook_workspace_in_config(pack.base_config, pack.repo_b)

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "workspace_root": str(pack.repo_b),
                "mcp_server": spec.mcp_server,
                "tool_name": spec.tool_name,
                "input_schema": spec.schema,
                "args": spec.repo_b_capture,
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tool_examples_record(request)
    assert response.status_code == 204

    assert capture_count(pack.db_path, pack.repo_b, spec) == 1
    assert capture_count(pack.db_path, pack.repo_a, spec) == 0

    store = ToolExamplesStore.open(str(pack.db_path))
    try:
        project_b = store.get_or_create_project(str(pack.repo_b.resolve()))
        captures = store.list_captures(project_b, spec.mcp_server, spec.tool_name)
        assert len(captures) == 1
        assert captures[0].input_json == spec.repo_b_capture
    finally:
        store.close()
