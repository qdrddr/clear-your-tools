"""Unit tests for pre-tool deny inline definition dedup (fixture-driven)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt_client.session_pre_tool_exposure import is_tool_definition_pre_exposed_for_deny
from tests.support.pre_tool_deny_dedup_fixtures import (
    assert_validation_expectation,
    build_pre_tool_payload,
    load_tools,
    load_unit_scenarios,
    patch_session_log,
    run_validate,
    write_session_log_from_template,
)

_UNIT_SCENARIOS = load_unit_scenarios()


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in _UNIT_SCENARIOS],
    ids=[scenario.id for scenario in _UNIT_SCENARIOS],
)
def test_pre_tool_deny_dedup_scenarios(
    scenario_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(item for item in _UNIT_SCENARIOS if item.id == scenario_id)
    log_path = tmp_path / f"{scenario_id}.jsonl"
    write_session_log_from_template(log_path, scenario.session_log)
    patch_session_log(monkeypatch, log_path)

    payload = build_pre_tool_payload(scenario)
    validation = run_validate(payload)
    assert_validation_expectation(validation, scenario.expect)


def test_is_tool_definition_pre_exposed_for_deny_detects_type1_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    write_session_log_from_template(log_path, "type2_with_type1_deny_entry")
    patch_session_log(monkeypatch, log_path)

    tools = load_tools()
    tool_record = dict(tools["filesystem_read_file"])
    tool_record["name"] = "filesystem_read_file"
    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "pre-tool-deny-dedup-session",
        "tool_name": "filesystem_read_file",
        "tool_input": {"bogus": "x"},
    }

    assert is_tool_definition_pre_exposed_for_deny(
        payload,
        catalog="cyt_mcp",
        tool_record=tool_record,
    )


def test_is_tool_definition_pre_exposed_for_deny_false_on_fresh_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    write_session_log_from_template(log_path, "type2_filesystem_read_file")
    patch_session_log(monkeypatch, log_path)

    tools = load_tools()
    tool_record = dict(tools["filesystem_read_file"])
    tool_record["name"] = "filesystem_read_file"
    payload = build_pre_tool_payload(_UNIT_SCENARIOS[0])

    assert not is_tool_definition_pre_exposed_for_deny(
        payload,
        catalog="cyt_mcp",
        tool_record=tool_record,
    )


def test_is_tool_definition_pre_exposed_for_deny_detects_prior_deny_json_in_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    write_session_log_from_template(log_path, "type2_filesystem_read_file")
    patch_session_log(monkeypatch, log_path)

    scenario = next(item for item in _UNIT_SCENARIOS if item.id == "prior_deny_json_in_prompt_omits_definition")
    tools = load_tools()
    tool_record = dict(tools["filesystem_read_file"])
    tool_record["name"] = "filesystem_read_file"
    payload = build_pre_tool_payload(scenario)

    assert is_tool_definition_pre_exposed_for_deny(
        payload,
        catalog="cyt_mcp",
        tool_record=tool_record,
    )
