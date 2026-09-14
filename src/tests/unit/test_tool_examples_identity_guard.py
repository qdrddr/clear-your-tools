"""Regression tests: tool_input_schema must always have valid mcp_server and tool_name."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.injection.session_log_build import _tool_record_core_for_catalog_bundle
from cyt.tool_examples.identity import is_valid_tool_example_identity, resolve_mcp_server_and_tool
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore
from cyt_client.tool_gate import extract_post_tool_example_capture
from tests.support.tool_examples_identity_fixtures import (
    SessionCatalogScenario,
    ToolExamplesIdentityPack,
    count_invalid_identity_schemas,
    load_polluted_seed,
    load_scenarios,
    load_session_catalog_scenarios,
    seed_polluted_identity_db,
    tool_examples_identity_pack,
    write_session_catalog_log,
)

_SCHEMA = {"type": "object", "properties": {"pattern": {"type": "string"}}}


@pytest.fixture
def store(tmp_path: Path) -> ToolExamplesStore:
    return ToolExamplesStore.open(str(tmp_path / "tool_examples.db"))


@pytest.mark.parametrize(
    "identity",
    load_scenarios()["valid_identities"],
    ids=lambda item: f"{item['mcp_server']}_{item['tool_name']}",
)
def test_fixture_valid_identities_pass_validation(identity: dict[str, str]) -> None:
    assert is_valid_tool_example_identity(identity["mcp_server"], identity["tool_name"])


@pytest.mark.parametrize(
    "identity",
    load_scenarios()["invalid_identities"],
    ids=lambda item: f"{item['mcp_server'] or 'empty'}_{item['tool_name'] or 'empty'}",
)
def test_fixture_invalid_identities_fail_validation(identity: dict[str, str]) -> None:
    assert not is_valid_tool_example_identity(identity["mcp_server"], identity["tool_name"])


@pytest.mark.parametrize(
    "scenario",
    load_session_catalog_scenarios(),
    ids=lambda item: item.id,
)
def test_session_catalog_capture_identity_from_fixtures(
    scenario: SessionCatalogScenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    write_session_catalog_log(log_path, catalog_tool=scenario.catalog_tool)
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    from tests.support.tool_examples_misparsed_fixtures import load_server_keys

    monkeypatch.setattr(
        "cyt_mcp.config.load_known_mcp_server_keys",
        lambda **_kwargs: list(load_server_keys()),
    )
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": scenario.payload_tool_name,
        "tool_input": scenario.tool_input,
        "tool_output": json.dumps({"results": []}),
    }
    capture = extract_post_tool_example_capture(payload)
    if scenario.expected_mcp_server is None:
        assert capture is None
        return
    assert capture is not None
    assert capture["mcp_server"] == scenario.expected_mcp_server
    assert capture["tool_name"] == scenario.expected_tool_name


def test_session_log_always_includes_tool_name_when_wire_equals_bare() -> None:
    record = _tool_record_core_for_catalog_bundle(
        {
            "name": "grep",
            "server_key": "fff",
            "tool_name": "grep",
            "input_schema": _SCHEMA,
        },
        catalog="cyt_mcp",
    )
    assert record["server_key"] == "fff"
    assert record["tool_name"] == "grep"
    assert resolve_mcp_server_and_tool(record) == ("fff", "grep")


def test_record_skips_invalid_identity(
    tool_examples_identity_pack: ToolExamplesIdentityPack,
) -> None:
    before = count_invalid_identity_schemas(tool_examples_identity_pack.user_examples_db_path)
    assert (
        record_tool_examples_capture(
            workspace=tool_examples_identity_pack.workspace,
            mcp_server="unknown",
            tool_name="grep",
            input_schema=_SCHEMA,
            args={"pattern": "x"},
            config=tool_examples_identity_pack.config,
        )
        is None
    )
    assert count_invalid_identity_schemas(tool_examples_identity_pack.user_examples_db_path) == before


def test_upsert_capture_rejects_invalid_identity(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    with pytest.raises(ValueError, match="invalid tool example identity"):
        store.upsert_capture(
            project_id,
            "unknown",
            "grep",
            _SCHEMA,
            {"pattern": "x"},
        )


def test_purge_invalid_identity_schemas_removes_polluted_seed(
    tool_examples_identity_pack: ToolExamplesIdentityPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_identity_db(
        db_path=tool_examples_identity_pack.user_examples_db_path,
        real_project_root=tool_examples_identity_pack.workspace,
        seed=seed,
    )
    before = count_invalid_identity_schemas(tool_examples_identity_pack.user_examples_db_path)
    assert before >= len(seed["invalid_schemas"])

    store = ToolExamplesStore(str(tool_examples_identity_pack.user_examples_db_path))
    try:
        removed = store.purge_invalid_identity_schemas()
        assert removed >= len(seed["invalid_schemas"])
        assert count_invalid_identity_schemas(tool_examples_identity_pack.user_examples_db_path) == 0
    finally:
        store.close()


def test_open_purges_invalid_identity_on_user_db(
    tool_examples_identity_pack: ToolExamplesIdentityPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_identity_db(
        db_path=tool_examples_identity_pack.user_examples_db_path,
        real_project_root=tool_examples_identity_pack.workspace,
        seed=seed,
    )
    with patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True):
        ToolExamplesStore.open(str(tool_examples_identity_pack.user_examples_db_path)).close()
    assert count_invalid_identity_schemas(tool_examples_identity_pack.user_examples_db_path) == 0
