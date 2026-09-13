"""Shared fixture loader for pre-tool deny definition dedup tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest

from cyt_client.cli import _handle_pre_tool
from cyt_client.sessions import entries_after_latest_compaction, read_session_log_file
from cyt_client.tool_gate import PreToolValidation, validate_pre_tool_call

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "pre_tool_deny_dedup"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
SESSION_LOGS_DIR = FIXTURES_ROOT / "session_logs"
TOOLS_PATH = FIXTURES_ROOT / "tools.json"

_DENY_DEFINITION_MARKER = "Correct tool definition:"
_PRE_TOOL_DENY_SOURCE = "cyt-client_pre-tool-deny"


@dataclass(frozen=True)
class DenyExpectation:
    allowed: bool | None
    includes_definition: bool | None
    includes_reason: bool | None
    reason_contains: tuple[str, ...]


@dataclass(frozen=True)
class DenyScenario:
    id: str
    session_log: str
    tool: str
    tool_input: dict[str, Any] | None
    shell_command: str | None
    prompt: str | None
    expect: DenyExpectation


@dataclass(frozen=True)
class DenyIntegrationStep:
    action: Literal["validate", "handle_pre_tool"]
    expect: DenyExpectation


@dataclass(frozen=True)
class DenyPersistExpectation:
    post_compaction_deny_entries: int
    tool_name: str


@dataclass(frozen=True)
class DenyIntegrationScenario:
    id: str
    session_log: str
    tool: str
    tool_input: dict[str, Any] | None
    shell_command: str | None
    prompt: str | None
    steps: tuple[DenyIntegrationStep, ...]
    expect_persist: DenyPersistExpectation | None


def _parse_expectation(raw: dict[str, Any]) -> DenyExpectation:
    return DenyExpectation(
        allowed=raw.get("allowed"),
        includes_definition=raw.get("includes_definition"),
        includes_reason=raw.get("includes_reason"),
        reason_contains=tuple(str(item) for item in raw.get("reason_contains", [])),
    )


def load_tools(path: Path = TOOLS_PATH) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"tools fixture must be an object: {path}")
    return payload


def load_unit_scenarios(path: Path = SCENARIOS_PATH) -> tuple[DenyScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[DenyScenario] = []
    for row in payload.get("scenarios", []):
        scenarios.append(
            DenyScenario(
                id=str(row["id"]),
                session_log=str(row["session_log"]),
                tool=str(row["tool"]),
                tool_input=row.get("tool_input") if isinstance(row.get("tool_input"), dict) else None,
                shell_command=str(row["shell_command"]) if row.get("shell_command") else None,
                prompt=str(row["prompt"]) if row.get("prompt") else None,
                expect=_parse_expectation(row["expect"]),
            ),
        )
    return tuple(scenarios)


def load_integration_scenarios(path: Path = SCENARIOS_PATH) -> tuple[DenyIntegrationScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[DenyIntegrationScenario] = []
    for row in payload.get("integration_scenarios", []):
        steps = tuple(
            DenyIntegrationStep(
                action=step["action"],
                expect=_parse_expectation(step["expect"]),
            )
            for step in row.get("steps", [])
        )
        persist_raw = row.get("expect_persist")
        expect_persist = None
        if isinstance(persist_raw, dict):
            expect_persist = DenyPersistExpectation(
                post_compaction_deny_entries=int(persist_raw["post_compaction_deny_entries"]),
                tool_name=str(persist_raw["tool_name"]),
            )
        scenarios.append(
            DenyIntegrationScenario(
                id=str(row["id"]),
                session_log=str(row["session_log"]),
                tool=str(row["tool"]),
                tool_input=row.get("tool_input") if isinstance(row.get("tool_input"), dict) else None,
                shell_command=str(row["shell_command"]) if row.get("shell_command") else None,
                prompt=str(row["prompt"]) if row.get("prompt") else None,
                steps=steps,
                expect_persist=expect_persist,
            ),
        )
    return tuple(scenarios)


def session_log_template_path(template_name: str) -> Path:
    path = SESSION_LOGS_DIR / f"{template_name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown session log template: {template_name}")
    return path


def write_session_log_from_template(path: Path, template_name: str) -> None:
    entries = json.loads(session_log_template_path(template_name).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"Session log template must be a JSON array: {template_name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry, separators=(",", ":")) for entry in entries) + "\n",
        encoding="utf-8",
    )


def patch_session_log(monkeypatch: pytest.MonkeyPatch, log_path: Path | None) -> None:
    def _resolver(_payload: dict) -> Path | None:
        return log_path

    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", _resolver)
    monkeypatch.setattr("cyt_client.sessions.session_log_path", _resolver)
    monkeypatch.setattr("cyt_client.session_pre_tool_exposure.session_log_path", _resolver)


def build_pre_tool_payload(
    scenario: DenyScenario | DenyIntegrationScenario,
    *,
    session_id: str = "pre-tool-deny-dedup-session",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event_name": "preToolUse",
        "session_id": session_id,
        "tool_name": scenario.tool,
    }
    if scenario.tool_input is not None:
        payload["tool_input"] = dict(scenario.tool_input)
    if scenario.shell_command:
        payload["tool_input"] = {"command": scenario.shell_command}
    if scenario.prompt:
        payload["prompt"] = scenario.prompt
    return payload


def run_validate(payload: dict[str, Any]) -> PreToolValidation:
    return validate_pre_tool_call(payload)


def run_handle_pre_tool(payload: dict[str, Any]) -> PreToolValidation:
    validation = validate_pre_tool_call(payload)
    assert validation.allowed is False
    with pytest.raises(SystemExit) as exc_info:
        _handle_pre_tool(payload, cursor_output=False)
    assert exc_info.value.code == 2
    return validation


def assert_deny_expectation(reason: str, expect: DenyExpectation) -> None:
    if expect.includes_reason is True:
        assert reason.strip()
    if expect.includes_definition is True:
        assert _DENY_DEFINITION_MARKER in reason
    if expect.includes_definition is False:
        assert _DENY_DEFINITION_MARKER not in reason
    for fragment in expect.reason_contains:
        assert fragment in reason


def assert_validation_expectation(validation: PreToolValidation, expect: DenyExpectation) -> None:
    if expect.allowed is not None:
        assert validation.allowed is expect.allowed
    assert_deny_expectation(validation.reason, expect)


def count_post_compaction_deny_entries(
    log_path: Path,
    *,
    tool_name: str,
) -> int:
    _agent, entries = read_session_log_file(log_path)
    post_compaction = entries_after_latest_compaction(entries)
    return sum(
        1
        for entry in post_compaction
        if entry.get("kind") == "tool"
        and entry.get("source") == _PRE_TOOL_DENY_SOURCE
        and entry.get("name") == tool_name
    )
