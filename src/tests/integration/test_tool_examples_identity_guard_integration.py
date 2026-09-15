"""Integration regression: tool_input_schema rows must always have valid identity."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore
from cyt_client.tool_gate import extract_post_tool_example_capture
from tests.support.tool_examples_identity_fixtures import (
    IdentityIntegrationScenario,
    ToolExamplesIdentityPack,
    count_invalid_identity_schemas,
    load_integration_scenarios,
    load_polluted_seed,
    load_session_catalog_scenarios,
    materialize_identity_pack,
    seed_polluted_identity_db,
    write_session_catalog_log,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda item: item.id,
)
def test_tool_examples_identity_guard_integration_scenarios(
    scenario: IdentityIntegrationScenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if scenario.id == "post_tool_use_capture_persists_valid_identity":
        _assert_post_tool_use_persists_valid_identity(tmp_path, monkeypatch)
    elif scenario.id == "open_purges_invalid_identity_seed":
        _assert_open_purges_invalid_identity_seed(tmp_path)
    elif scenario.id == "record_rejects_invalid_identity":
        _assert_record_rejects_invalid_identity(tmp_path)
    else:
        raise AssertionError(f"unknown integration scenario: {scenario.id}")


def _assert_post_tool_use_persists_valid_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_identity_pack(tmp_path / "guard")
    scenario = next(
        item
        for item in load_session_catalog_scenarios()
        if item.id == "wire_equals_bare_legacy_session_log"
    )
    log_path = tmp_path / "guard" / "session.jsonl"
    write_session_catalog_log(log_path, catalog_tool=scenario.catalog_tool)
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    from tests.support.tool_examples_misparsed_fixtures import load_server_keys

    monkeypatch.setattr(
        "cyt_mcp.config.load_known_mcp_server_keys",
        lambda **_kwargs: list(load_server_keys()),
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
    assert count_invalid_identity_schemas(pack.user_examples_db_path) == 0

    conn = sqlite3.connect(str(pack.user_examples_db_path))
    try:
        row = conn.execute(
            "SELECT mcp_server, tool_name FROM tool_input_schema WHERE schema_id = ?",
            (schema_id,),
        ).fetchone()
        assert row == (scenario.expected_mcp_server, scenario.expected_tool_name)
    finally:
        conn.close()


def _assert_open_purges_invalid_identity_seed(tmp_path: Path) -> None:
    pack = materialize_identity_pack(tmp_path / "guard")
    seed = load_polluted_seed()
    seed_polluted_identity_db(
        db_path=pack.user_examples_db_path,
        real_project_root=pack.workspace,
        seed=seed,
    )
    assert count_invalid_identity_schemas(pack.user_examples_db_path) >= len(
        seed["invalid_schemas"],
    )

    with patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True):
        ToolExamplesStore.open(str(pack.user_examples_db_path)).close()

    assert count_invalid_identity_schemas(pack.user_examples_db_path) == 0


def _assert_record_rejects_invalid_identity(tmp_path: Path) -> None:
    pack: ToolExamplesIdentityPack = materialize_identity_pack(tmp_path / "guard")
    schema = {"type": "object", "properties": {"pattern": {"type": "string"}}}
    assert (
        record_tool_examples_capture(
            workspace=pack.workspace,
            mcp_server="unknown",
            tool_name="grep",
            input_schema=schema,
            args={"pattern": "x"},
            config=pack.config,
        )
        is None
    )
    assert count_invalid_identity_schemas(pack.user_examples_db_path) == 0
