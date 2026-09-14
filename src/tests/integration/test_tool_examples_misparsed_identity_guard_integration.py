"""Integration regression: tool_examples.db must not retain misparsed identities."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore
from cyt_client.tool_gate import extract_post_tool_example_capture
from tests.support.tool_examples_misparsed_fixtures import (
    MisparsedIntegrationScenario,
    count_misparsed_schemas,
    count_orphan_examples,
    load_capture_tool_scenarios,
    load_integration_scenarios,
    load_polluted_seed,
    load_server_keys,
    materialize_misparsed_pack,
    seed_polluted_misparsed_db,
    write_capture_session_log,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda item: item.id,
)
def test_tool_examples_misparsed_identity_guard_integration_scenarios(
    scenario: MisparsedIntegrationScenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if scenario.id == "open_purges_misparsed_seed":
        _assert_open_purges_misparsed_seed(tmp_path)
    elif scenario.id == "open_purges_orphan_examples":
        _assert_open_purges_orphan_examples(tmp_path)
    elif scenario.id == "capture_uses_canonical_identity":
        _assert_capture_uses_canonical_identity(tmp_path, monkeypatch)
    else:
        raise AssertionError(f"unknown integration scenario: {scenario.id}")


def _assert_open_purges_misparsed_seed(tmp_path: Path) -> None:
    pack = materialize_misparsed_pack(tmp_path / "guard")
    server_keys = list(load_server_keys())
    seed = load_polluted_seed()
    seed_polluted_misparsed_db(
        db_path=pack.user_examples_db_path,
        real_project_root=pack.workspace,
        seed=seed,
    )
    assert count_misparsed_schemas(pack.user_examples_db_path, server_keys) >= len(
        seed["misparsed_schemas"],
    )

    with patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True), patch(
        "cyt_mcp.config.load_known_mcp_server_keys",
        return_value=server_keys,
    ):
        ToolExamplesStore.open(str(pack.user_examples_db_path)).close()

    assert count_misparsed_schemas(pack.user_examples_db_path, server_keys) == 0


def _assert_open_purges_orphan_examples(tmp_path: Path) -> None:
    pack = materialize_misparsed_pack(tmp_path / "guard")
    server_keys = list(load_server_keys())
    seed = load_polluted_seed()
    seed_polluted_misparsed_db(
        db_path=pack.user_examples_db_path,
        real_project_root=pack.workspace,
        seed=seed,
    )
    assert count_orphan_examples(pack.user_examples_db_path) >= 2

    with patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True), patch(
        "cyt_mcp.config.load_known_mcp_server_keys",
        return_value=server_keys,
    ):
        ToolExamplesStore.open(str(pack.user_examples_db_path)).close()

    assert count_orphan_examples(pack.user_examples_db_path) == 0


def _assert_capture_uses_canonical_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_misparsed_pack(tmp_path / "guard")
    scenario = load_capture_tool_scenarios()[0]
    server_keys = list(load_server_keys())
    log_path = tmp_path / "guard" / "session.jsonl"
    write_capture_session_log(log_path, catalog_tool=scenario.catalog_tool)
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    monkeypatch.setattr(
        "cyt_mcp.config.load_known_mcp_server_keys",
        lambda **_kwargs: server_keys,
    )

    capture = extract_post_tool_example_capture(
        {
            "hook_event_name": "postToolUse",
            "tool_name": scenario.payload_tool_name,
            "tool_input": scenario.tool_input,
            "tool_output": json.dumps({"results": []}),
        },
    )
    assert capture is not None
    schema_id = record_tool_examples_capture(
        workspace=pack.workspace,
        mcp_server=capture["mcp_server"],
        tool_name=capture["tool_name"],
        input_schema=capture["input_schema"],
        args=capture["args"],
        config=pack.config,
    )
    assert schema_id is not None

    conn = sqlite3.connect(str(pack.user_examples_db_path))
    try:
        row = conn.execute(
            "SELECT mcp_server, tool_name FROM tool_input_schema WHERE schema_id = ?",
            (schema_id,),
        ).fetchone()
        assert row == (scenario.expected_mcp_server, scenario.expected_tool_name)
        assert count_misparsed_schemas(pack.user_examples_db_path, server_keys) == 0
    finally:
        conn.close()
