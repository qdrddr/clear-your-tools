"""Gherkin steps for agent skill read interceptor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from pytest_bdd import given, scenarios, then, when

from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "agent_interceptor.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _skill_md(tmp_path: Path) -> Path:
    skill_dir = tmp_path / ".cursor" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo-skill\n---\n\n# Demo\n\nBody.\n", encoding="utf-8")
    return skill_path


@given("agent interceptor is enabled")
def given_interceptor_enabled(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.agent_interceptor.skills_hook_agent_interceptor_enabled",
        lambda: True,
    )
    gherkin_context.payload["_interceptor_enabled"] = True


@given("skills are enabled in config")
def given_skills_enabled(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cyt_client.config.skills_enabled", lambda: True)


@given("skills are disabled in config")
def given_skills_disabled(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cyt_client.config.skills_enabled", lambda: False)


@given("a preToolUse Read payload for path outside skill dirs")
def given_read_outside(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    other = tmp_path / "other.md"
    other.write_text("# Other\n", encoding="utf-8")
    gherkin_context.payload.update(
        {
            "hook_event_name": "preToolUse",
            "conversation_id": "conv-intercept",
            "workspace_roots": [str(tmp_path)],
            "tool_name": "Read",
            "tool_input": {"path": str(other)},
        },
    )


@given("a preToolUse Read payload for a skill file with offset")
def given_read_with_offset(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    skill_path = _skill_md(tmp_path)
    gherkin_context.payload.update(
        {
            "hook_event_name": "preToolUse",
            "conversation_id": "conv-intercept",
            "workspace_roots": [str(tmp_path)],
            "tool_name": "Read",
            "tool_input": {"path": str(skill_path), "offset": 1},
        },
    )


@given("a preToolUse Read payload for a skill file under skill dirs")
def given_read_skill(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    skill_path = _skill_md(tmp_path)
    gherkin_context.payload.update(
        {
            "_tmp": tmp_path,
            "_skill_path": skill_path,
            "hook_event_name": "preToolUse",
            "conversation_id": "conv-intercept",
            "workspace_roots": [str(tmp_path)],
            "tool_name": "Read",
            "tool_input": {"path": str(skill_path)},
        },
    )


def _session_log_path(gherkin_context: GherkinContext) -> Path:
    from cyt_client.sessions import session_log_path

    payload = {k: v for k, v in gherkin_context.payload.items() if not str(k).startswith("_")}
    with patch.dict("os.environ", {"CYT_LAUNCH_AGENT": "cursor"}, clear=False):
        path = session_log_path(payload)
    assert path is not None
    return path


@given("session log has no turn entries")
def given_no_turns(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    from cyt_client.sessions import append_session_log

    log_path = _session_log_path(gherkin_context)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    append_session_log(
        log_path,
        [
            {
                "kind": "skill_directories",
                "key": "skill_directories",
                "directories": [str(tmp_path / ".cursor" / "skills")],
            },
        ],
        agent="cursor",
    )
    gherkin_context.payload["_session_log"] = log_path


@given("session log has a turn with user and assistant text")
def given_turn(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    from cyt_client.sessions import append_session_log

    log_path = _session_log_path(gherkin_context)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path = gherkin_context.payload.get("_skill_path") or _skill_md(tmp_path)
    append_session_log(
        log_path,
        [
            {
                "kind": "skill_directories",
                "key": "skill_directories",
                "directories": [str(tmp_path / ".cursor" / "skills")],
            },
            {
                "kind": "turn",
                "key": "turn:test",
                "prompt": "how do hooks work",
                "assistant": "checking skill file",
            },
        ],
        agent="cursor",
    )
    gherkin_context.payload["_session_log"] = log_path
    gherkin_context.payload["_skill_path"] = skill_path


@given("session log has a prompt-injected skill entry for that file")
def given_prompt_skill(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    from cyt_client.agent_interceptor import skill_item_key_for_path
    from cyt_client.sessions import append_session_log

    skill_path = gherkin_context.payload.get("_skill_path") or _skill_md(tmp_path)
    log_path = _session_log_path(gherkin_context)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    key = skill_item_key_for_path(skill_path)
    append_session_log(
        log_path,
        [
            {
                "kind": "skill_directories",
                "key": "skill_directories",
                "directories": [str(tmp_path / ".cursor" / "skills")],
            },
            {"kind": "turn", "key": "turn:test", "prompt": "use skill", "assistant": ""},
            {
                "kind": "skill",
                "key": key,
                "hash": "abc",
                "full": False,
                "source": "file",
                "body": "# Demo",
                "path": str(skill_path),
            },
        ],
        agent="cursor",
    )
    gherkin_context.payload["_session_log"] = log_path
    gherkin_context.payload["cyt_transcript"] = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "use skill"}]}},
    ]


@given("session log has three skinny skill entries for that file")
def given_three_skinny(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    from cyt_client.agent_interceptor import skill_item_key_for_path
    from cyt_client.sessions import append_session_log

    skill_path = gherkin_context.payload.get("_skill_path") or _skill_md(tmp_path)
    key = skill_item_key_for_path(skill_path)
    entries = [
        {
            "kind": "skill",
            "key": key,
            "hash": "abc",
            "full": False,
            "source": "file",
            "body": f"# Demo {idx}",
            "path": str(skill_path),
        }
        for idx in range(3)
    ]
    log_path = _session_log_path(gherkin_context)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    append_session_log(
        log_path,
        [
            {
                "kind": "skill_directories",
                "key": "skill_directories",
                "directories": [str(tmp_path / ".cursor" / "skills")],
            },
            *entries,
        ],
        agent="cursor",
    )
    gherkin_context.payload["_session_log"] = log_path
    gherkin_context.payload["_skill_path"] = skill_path


@given("hook daemon returns intercept skinny response")
def given_daemon_skinny(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    skill_path = gherkin_context.payload["_skill_path"]
    skinny = tmp_path / ".cyt" / "skinny" / "conv-intercept" / "abc.md"
    skinny.parent.mkdir(parents=True, exist_ok=True)
    skinny.write_text("# Skinny\n", encoding="utf-8")
    gherkin_context.payload["_daemon_response"] = (
        200,
        json.dumps(
            {
                "agent_interceptor": True,
                "permission": "allow",
                "updated_input": {"path": str(skinny)},
                "skill_log_entry": {
                    "kind": "skill",
                    "key": f"skill:{skill_path}",
                    "hash": "abc123",
                    "full": False,
                    "source": "file",
                    "body": "# Skinny",
                    "path": str(skill_path),
                },
            },
        ).encode(),
    )


@given("hook daemon returns HTTP 500")
def given_daemon_500(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["_daemon_response"] = (500, b'{"error":"fail"}')


@when("cyt-client handles preToolUse")
def when_client_pre_tool(
    gherkin_context: GherkinContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.cli import main

    payload = {k: v for k, v in gherkin_context.payload.items() if not str(k).startswith("_")}
    daemon = cast(
        tuple[int, bytes],
        gherkin_context.payload.get("_daemon_response", (200, b"{}")),
    )
    gherkin_context.payload["_daemon_called"] = False

    def _record_post(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
        gherkin_context.payload["_daemon_called"] = True
        return daemon

    with patch(
        "cyt_client.agent_interceptor.skills_hook_agent_interceptor_enabled",
        return_value=True,
    ):
        with patch(
            "cyt_client.agent_interceptor.resolve_hook_url",
            return_value="http://127.0.0.1:8834/hook/connect",
        ):
            with patch("cyt_client.transport.post_hook_inject", side_effect=_record_post):
                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    with patch.dict("os.environ", {"CYT_LAUNCH_AGENT": "cursor"}, clear=False):
                        main()
    captured = capsys.readouterr()
    gherkin_context.stdout = captured.out
    gherkin_context.payload["_stdout_json"] = (
        json.loads(captured.out) if captured.out.strip() else {}
    )


@when("hook daemon handles preToolUse intercept")
def when_daemon_intercept(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    from cyt.config import load_config
    from cyt.skills.agent_interceptor import run_skill_read_intercept

    skill_path = _skill_md(tmp_path)
    config = load_config()
    config.setdefault("skills", {})["enabled"] = False
    payload = {
        "hook_event_name": "preToolUse",
        "cyt_intercept_read_path": str(skill_path),
        "cyt_intercept_query": "User_Asks: test",
        "conversation_id": "conv-daemon",
        "workspace_roots": [str(tmp_path)],
    }
    gherkin_context.payload["_intercept_result"] = run_skill_read_intercept(
        payload,
        config,
        pruner_settings=None,
    )


@then("preToolUse permission is allow")
def then_allow(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["_stdout_json"].get("permission") == "allow"


@then("preToolUse permission is deny")
def then_deny(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["_stdout_json"].get("permission") == "deny"


@then("hook daemon was not called")
def then_no_daemon(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("_daemon_called") is not True


@then("preToolUse updated_input points to skinny file")
def then_updated_input(gherkin_context: GherkinContext) -> None:
    updated = gherkin_context.payload["_stdout_json"].get("updated_input") or {}
    assert ".cyt/skinny" in str(updated.get("path", ""))


@then("preToolUse has no updated_input")
def then_no_updated(gherkin_context: GherkinContext) -> None:
    assert "updated_input" not in gherkin_context.payload["_stdout_json"]


@then("session log gains a skinny skill entry")
def then_skinny_logged(gherkin_context: GherkinContext) -> None:
    from cyt_client.sessions import read_session_log_file

    log_path = gherkin_context.payload["_session_log"]
    _agent, entries = read_session_log_file(log_path)
    skill_entries = [e for e in entries if e.get("kind") == "skill" and not e.get("full")]
    assert skill_entries


@then("intercept response permission is allow")
def then_intercept_allow(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["_intercept_result"]["permission"] == "allow"


@then("intercept response has no updated_input")
def then_intercept_no_updated(gherkin_context: GherkinContext) -> None:
    assert "updated_input" not in gherkin_context.payload["_intercept_result"]
