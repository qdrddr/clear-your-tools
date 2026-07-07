"""Tests for cyt-client hook HTTP forwarding."""

from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt_client import port as client_port
from cyt_client.transcript import enrich_hook_payload


def test_cyt_client_package_has_no_cyt_imports() -> None:
    for module_name in (
        "cyt_client.cli",
        "cyt_client.cursor",
        "cyt_client.port",
        "cyt_client.rules_file",
        "cyt_client.skills",
        "cyt_client.transport",
        "cyt_client.transcript",
    ):
        module = importlib.import_module(module_name)
        assert module.__file__ is not None
        source_path = Path(module.__file__).resolve()
        text = source_path.read_text(encoding="utf-8")
        assert "import cyt." not in text
        assert "from cyt." not in text


def test_is_hook_server_requires_hook_flag() -> None:
    assert client_port.is_hook_server({"name": "cyt", "status": "ok", "hook": True})
    assert not client_port.is_hook_server({"name": "cyt", "status": "ok"})
    assert not client_port.is_hook_server(None)


def test_resolve_hook_url_prefers_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_URL", "http://127.0.0.1:9999/hook/inject")
    with patch("cyt_client.port._hook_url_is_live", return_value=True):
        assert client_port.resolve_hook_url() == "http://127.0.0.1:9999/hook/inject"


def test_resolve_hook_url_ignores_stale_pidfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CYT_HOOK_URL", raising=False)
    pidfile = {
        "hook_url": "http://127.0.0.1:8834/hook/inject",
        "port": 8834,
    }
    with patch("cyt_client.port.read_hook_daemon_pidfile", return_value=pidfile):
        with patch("cyt_client.port._hook_url_is_live", return_value=False):
            with patch("cyt_client.port.find_hook_server_port", return_value=None):
                assert client_port.resolve_hook_url() is None


def test_cli_silent_on_http_error(capsys: pytest.CaptureFixture[str]) -> None:
    payload = (
        b'{"hook_event_name":"UserPromptSubmit","prompt":"hello","cwd":"/tmp/isolated-project"}'
    )
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject", return_value=(502, b"bad gateway")):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_silent_on_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    payload = b'{"hook_event_name":"UserPromptSubmit","prompt":"hello"}'
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject", side_effect=ConnectionError("refused")):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_writes_response_body_to_stdout_only(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch(
            "cyt_client.cli.post_hook_inject",
            return_value=(200, b'{"hookSpecificOutput":{"additionalContext":"x"}}'),
        ):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                main()

    captured = capsys.readouterr()
    assert captured.out == '{"hookSpecificOutput":{"additionalContext":"x"}}'
    assert captured.err == ""


def test_cli_reformats_cursor_before_submit_prompt_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        payload = {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": [str(workspace)],
        }
        with patch(
            "cyt_client.cli.resolve_hook_url",
            return_value="http://127.0.0.1:8834/hook/inject",
        ):
            inject_response = json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": "<agent-skills>skill text</agent-skills>",
                    },
                },
            ).encode()
            with patch(
                "cyt_client.cli.post_hook_inject",
                return_value=(200, inject_response),
            ):
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {
            "continue": True,
            "additional_context": "<agent-skills>skill text</agent-skills>",
        }
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        assert rules_path.is_file()
        assert "alwaysApply: true" in rules_path.read_text(encoding="utf-8")
        assert "<agent-skills>skill text</agent-skills>" in rules_path.read_text(encoding="utf-8")


def test_enrich_hook_payload_adds_cyt_agent_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_LAUNCH_AGENT", "cursor")
    raw = json.dumps(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": ["/tmp/project"],
        },
    ).encode()
    enriched = json.loads(enrich_hook_payload(raw))
    assert enriched["hook_event_name"] == "beforeSubmitPrompt"
    assert enriched["cyt_agent"] == "cursor"
    assert "cyt_skills" in enriched


def test_cli_silent_when_server_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    payload = (
        b'{"hook_event_name":"UserPromptSubmit","prompt":"hello","cwd":"/tmp/isolated-project"}'
    )
    with patch("cyt_client.cli.resolve_hook_url", return_value=None):
        from cyt_client.cli import main

        with patch("sys.stdin.buffer.read", return_value=payload):
            main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_cursor_returns_continue_when_server_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": ["/tmp/project"],
        },
    ).encode()
    with patch("cyt_client.cli.resolve_hook_url", return_value=None):
        from cyt_client.cli import main

        with patch("sys.stdin.buffer.read", return_value=payload):
            main()

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"continue": True}
    assert captured.err == ""


def test_format_hook_stdout_roundtrip() -> None:
    from cyt.skills.cli import format_hook_stdout

    payload = {"hook_event_name": "UserPromptSubmit"}
    formatted = format_hook_stdout("hello", payload)
    data = json.loads(formatted)
    assert data["hookSpecificOutput"]["additionalContext"] == "hello"


def test_enrich_hook_payload_adds_cyt_transcript_from_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            "\n".join(
                (
                    '{"id": 1, "name": "Damien"}',
                    '{"id": 2, "name": "Alex"}',
                    '{"id": 3, "name": "Sam"}',
                ),
            ),
            encoding="utf-8",
        )
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "payload": {
                "prompt": "hello",
                "transcript_path": str(transcript),
            },
        }
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert enriched["cyt_transcript"] == [
            {"id": 1, "name": "Damien"},
            {"id": 2, "name": "Alex"},
            {"id": 3, "name": "Sam"},
        ]


def test_enrich_hook_payload_omits_cyt_transcript_without_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HOME", str(Path(tmp) / "home"))
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "cwd": str(Path(tmp) / "project"),
        }
        raw = json.dumps(payload).encode()
        enriched = json.loads(enrich_hook_payload(raw))
        assert "cyt_transcript" not in enriched
        assert "cyt_skills" in enriched


def test_enrich_hook_payload_adds_whole_json_file_as_one_item() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.json"
        transcript.write_text(
            json.dumps([{"id": 1, "name": "Damien"}, {"id": 2, "name": "Alex"}]),
            encoding="utf-8",
        )
        payload = {"hook_event_name": "UserPromptSubmit", "transcript_path": str(transcript)}
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert enriched["cyt_transcript"] == [
            [{"id": 1, "name": "Damien"}, {"id": 2, "name": "Alex"}],
        ]


def test_enrich_hook_payload_falls_back_to_text_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "notes.txt"
        transcript.write_text("line one\nline two\n", encoding="utf-8")
        payload = {"hook_event_name": "UserPromptSubmit", "transcript_path": str(transcript)}
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert enriched["cyt_transcript"] == ["line one\nline two\n"]


def test_cli_enriches_transcript_before_post(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text('{"id": 1, "name": "Damien"}\n', encoding="utf-8")
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(transcript),
            "prompt": "hello",
        }
        with patch(
            "cyt_client.cli.resolve_hook_url",
            return_value="http://127.0.0.1:8834/hook/inject",
        ):
            with patch("cyt_client.cli.post_hook_inject", return_value=(200, b"")) as post:
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main()

                sent = json.loads(post.call_args.args[1])
                assert sent["cyt_transcript"] == [{"id": 1, "name": "Damien"}]
                assert "cyt_skills" in sent
                assert isinstance(sent["cyt_skills"], list)

    capsys.readouterr()


def test_build_rules_mdc_includes_always_apply() -> None:
    from cyt_client.rules_file import build_rules_mdc

    text = build_rules_mdc("<agent-skills>demo</agent-skills>")
    assert "alwaysApply: true" in text
    assert "<agent-skills>demo</agent-skills>" in text


def test_sync_cursor_rules_file_skips_unchanged_content() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path, sync_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        injection = "<agent-tools>demo</agent-tools>"
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_rules_mdc(injection), encoding="utf-8")

        assert sync_cursor_rules_file(workspace, injection) is False


def test_sync_cursor_rules_file_deletes_on_empty_injection() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path, sync_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_rules_mdc("demo"), encoding="utf-8")

        assert sync_cursor_rules_file(workspace, "") is True
        assert not path.is_file()


def test_delete_cursor_rules_file_noop_when_absent() -> None:
    from cyt_client.rules_file import delete_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        assert delete_cursor_rules_file(Path(tmp)) is False


def test_ensure_gitignore_entry_is_idempotent() -> None:
    from cyt_client.rules_file import GITIGNORE_ENTRY, ensure_gitignore_entry

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        (workspace / ".git").mkdir()
        ensure_gitignore_entry(workspace)
        ensure_gitignore_entry(workspace)
        lines = (workspace / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert lines.count(GITIGNORE_ENTRY) == 1


def test_cli_session_start_deletes_rules_file_without_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("stale", encoding="utf-8")
        payload = json.dumps(
            {
                "hook_event_name": "sessionStart",
                "workspace_roots": [str(workspace)],
            },
        ).encode()
        with patch("cyt_client.cli.post_hook_inject") as post:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

        post.assert_not_called()
        assert not rules_path.is_file()
        assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_cli_session_end_deletes_rules_file_without_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("stale", encoding="utf-8")
        payload = json.dumps(
            {
                "hook_event_name": "sessionEnd",
                "workspace_roots": [str(workspace)],
            },
        ).encode()
        with patch("cyt_client.cli.post_hook_inject") as post:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

        post.assert_not_called()
        assert not rules_path.is_file()
        assert json.loads(capsys.readouterr().out) == {"continue": True}
