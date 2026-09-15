"""Regression tests: tool_input_schema must use canonical wire-name identities."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tool_examples.store import ToolExamplesStore
from cyt_client.tool_gate import extract_post_tool_example_capture
from cyt_mcp.tool_identity import canonical_backend_identity, is_canonical_schema_identity
from tests.support.tool_examples_misparsed_fixtures import (
    CaptureToolScenario,
    ToolExamplesMisparsedPack,
    count_misparsed_schemas,
    count_orphan_examples,
    load_capture_tool_scenarios,
    load_polluted_seed,
    load_server_keys,
    seed_polluted_misparsed_db,
    write_capture_session_log,
)


@pytest.fixture
def server_keys() -> list[str]:
    return list(load_server_keys())


@pytest.mark.parametrize(
    "row",
    load_polluted_seed()["misparsed_schemas"],
    ids=lambda item: f"{item['mcp_server']}_{item['tool_name']}",
)
def test_fixture_misparsed_identities_are_not_canonical(
    row: dict[str, str],
    server_keys: list[str],
) -> None:
    assert not is_canonical_schema_identity(
        row["mcp_server"],
        row["tool_name"],
        server_keys,
    )


@pytest.mark.parametrize(
    "row",
    load_polluted_seed()["canonical_schemas"],
    ids=lambda item: f"{item['mcp_server']}_{item['tool_name']}",
)
def test_fixture_canonical_identities_pass_validation(
    row: dict[str, str],
    server_keys: list[str],
) -> None:
    assert is_canonical_schema_identity(
        row["mcp_server"],
        row["tool_name"],
        server_keys,
    )


@pytest.mark.parametrize(
    "scenario",
    load_capture_tool_scenarios(),
    ids=lambda item: item.id,
)
def test_canonical_backend_identity_overrides_wrong_explicit_fields(
    scenario: CaptureToolScenario,
    server_keys: list[str],
) -> None:
    server, bare = canonical_backend_identity(scenario.catalog_tool, server_keys)
    assert server == scenario.expected_mcp_server
    assert bare == scenario.expected_tool_name


@pytest.mark.parametrize(
    "scenario",
    load_capture_tool_scenarios(),
    ids=lambda item: item.id,
)
def test_capture_tool_scenarios_use_canonical_identity_from_fixtures(
    scenario: CaptureToolScenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_keys: list[str],
) -> None:
    log_path = tmp_path / "session.jsonl"
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
    assert capture["mcp_server"] == scenario.expected_mcp_server
    assert capture["tool_name"] == scenario.expected_tool_name


def test_purge_misparsed_identity_schemas_removes_polluted_seed(
    tool_examples_misparsed_pack: ToolExamplesMisparsedPack,
    server_keys: list[str],
) -> None:
    seed = load_polluted_seed()
    seed_polluted_misparsed_db(
        db_path=tool_examples_misparsed_pack.user_examples_db_path,
        real_project_root=tool_examples_misparsed_pack.workspace,
        seed=seed,
    )
    before = count_misparsed_schemas(
        tool_examples_misparsed_pack.user_examples_db_path,
        server_keys,
    )
    assert before >= len(seed["misparsed_schemas"])

    store = ToolExamplesStore(str(tool_examples_misparsed_pack.user_examples_db_path))
    try:
        removed = store.purge_misparsed_identity_schemas(server_keys)
        assert removed >= len(seed["misparsed_schemas"])
        assert (
            count_misparsed_schemas(tool_examples_misparsed_pack.user_examples_db_path, server_keys)
            == 0
        )
    finally:
        store.close()


def test_purge_orphan_examples_removes_seed_orphans(
    tool_examples_misparsed_pack: ToolExamplesMisparsedPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_misparsed_db(
        db_path=tool_examples_misparsed_pack.user_examples_db_path,
        real_project_root=tool_examples_misparsed_pack.workspace,
        seed=seed,
    )
    assert count_orphan_examples(tool_examples_misparsed_pack.user_examples_db_path) >= 2

    store = ToolExamplesStore(str(tool_examples_misparsed_pack.user_examples_db_path))
    try:
        removed = store.purge_orphan_examples()
        assert removed >= 2
        assert count_orphan_examples(tool_examples_misparsed_pack.user_examples_db_path) == 0
    finally:
        store.close()


def test_open_purges_misparsed_and_orphans_on_user_db(
    tool_examples_misparsed_pack: ToolExamplesMisparsedPack,
    server_keys: list[str],
) -> None:
    seed = load_polluted_seed()
    seed_polluted_misparsed_db(
        db_path=tool_examples_misparsed_pack.user_examples_db_path,
        real_project_root=tool_examples_misparsed_pack.workspace,
        seed=seed,
    )
    with (
        patch("cyt.tool_examples.store.is_default_user_cyt_db", return_value=True),
        patch(
            "cyt_mcp.config.load_known_mcp_server_keys",
            return_value=server_keys,
        ),
    ):
        ToolExamplesStore.open(str(tool_examples_misparsed_pack.user_examples_db_path)).close()

    assert (
        count_misparsed_schemas(tool_examples_misparsed_pack.user_examples_db_path, server_keys)
        == 0
    )
    assert count_orphan_examples(tool_examples_misparsed_pack.user_examples_db_path) == 0
