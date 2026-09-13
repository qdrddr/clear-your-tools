"""Integration tests: pre-tool deny definition dedup through cyt-client hook path."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.pre_tool_deny_dedup_fixtures import (
    assert_deny_expectation,
    assert_validation_expectation,
    build_pre_tool_payload,
    count_post_compaction_deny_entries,
    load_integration_scenarios,
    patch_session_log,
    run_handle_pre_tool,
    run_validate,
    write_session_log_from_template,
)

_INTEGRATION_SCENARIOS = load_integration_scenarios()


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in _INTEGRATION_SCENARIOS],
    ids=[scenario.id for scenario in _INTEGRATION_SCENARIOS],
)
def test_pre_tool_deny_dedup_integration_flows(
    scenario_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(item for item in _INTEGRATION_SCENARIOS if item.id == scenario_id)
    log_path = tmp_path / f"{scenario_id}.jsonl"
    write_session_log_from_template(log_path, scenario.session_log)
    patch_session_log(monkeypatch, log_path)

    payload = build_pre_tool_payload(scenario)
    last_reason = ""

    for step in scenario.steps:
        if step.action == "validate":
            validation = run_validate(payload)
            assert_validation_expectation(validation, step.expect)
            last_reason = validation.reason
        elif step.action == "handle_pre_tool":
            validation = run_handle_pre_tool(payload)
            assert_deny_expectation(validation.reason, step.expect)
            last_reason = validation.reason
        else:
            raise AssertionError(f"Unknown step action: {step.action}")

    assert last_reason.strip()

    if scenario.expect_persist is not None:
        count = count_post_compaction_deny_entries(
            log_path,
            tool_name=scenario.expect_persist.tool_name,
        )
        assert count == scenario.expect_persist.post_compaction_deny_entries


def test_integration_unknown_tool_unaffected_by_definition_dedup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown-tool denies still surface hints even when session has Type-1 entries."""
    log_path = tmp_path / "unknown-tool.jsonl"
    write_session_log_from_template(log_path, "type2_with_type1_deny_entry")
    patch_session_log(monkeypatch, log_path)

    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "pre-tool-deny-dedup-session",
        "tool_name": "filesystem_write_file",
        "tool_input": {"path": "/tmp/x"},
    }
    validation = run_validate(payload)
    assert validation.allowed is False
    assert "get-tool-definitions" in validation.reason
    assert "Correct tool definition:" not in validation.reason
