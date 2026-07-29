"""Tests for cyt-client hook HTTP forwarding.

Gherkin equivalents: ``src/tests/unit/gherkin/features/cyt_client.feature``.
"""

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
from cyt_client.transport import post_hook_inject, post_timeout_seconds


def test_cyt_client_package_has_no_cyt_imports() -> None:
    for module_name in (
        "cyt_client.agent",
        "cyt_client.cli",
        "cyt_client.config",
        "cyt_client.cursor",
        "cyt_client.port",
        "cyt_client.rules_file",
        "cyt_client.sessions",
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


def test_post_hook_inject_timeout_raises_connection_error() -> None:
    with patch("cyt_client.transport.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(ConnectionError, match=r"timed out after"):
            post_hook_inject("http://127.0.0.1:8834/hook/inject", b"{}")


def test_post_timeout_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_POST_TIMEOUT_SECONDS", "90")
    assert post_timeout_seconds() == 90.0


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


def test_cli_verbose_logs_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    payload = b'{"hook_event_name":"UserPromptSubmit","prompt":"hello"}'
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject", side_effect=ConnectionError("refused")):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main(["--verbose"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hook server connection failed" in captured.err


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
    from cyt_client.rules_file import reset_rules_file_rel_path

    try:
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
            assert "<agent-skills>skill text</agent-skills>" in rules_path.read_text(
                encoding="utf-8",
            )
    finally:
        reset_rules_file_rel_path()


def test_cli_cursor_before_submit_writes_custom_rule_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.rules_file import reset_rules_file_rel_path

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            custom_rules = workspace / ".cursor" / "rules" / "cyt-indexer.mdc"
            default_rules = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
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
                            "additionalContext": "<agent-skills>custom path</agent-skills>",
                        },
                    },
                ).encode()
                with patch(
                    "cyt_client.cli.post_hook_inject",
                    return_value=(200, inject_response),
                ):
                    from cyt_client.cli import main

                    with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                        main(["--rule", ".cursor/rules/cyt-indexer.mdc"])

            captured = capsys.readouterr()
            assert json.loads(captured.out) == {
                "continue": True,
                "additional_context": "<agent-skills>custom path</agent-skills>",
            }
            assert custom_rules.is_file()
            assert "<agent-skills>custom path</agent-skills>" in custom_rules.read_text(
                encoding="utf-8",
            )
            assert not default_rules.is_file()
    finally:
        reset_rules_file_rel_path()


def test_cli_rule_flag_ignored_for_non_cursor_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.rules_file import reset_rules_file_rel_path

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            custom_rules = workspace / ".cursor" / "rules" / "cyt-indexer.mdc"
            payload = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "hello",
                "cwd": str(workspace),
            }
            with patch(
                "cyt_client.cli.resolve_hook_url",
                return_value="http://127.0.0.1:8834/hook/inject",
            ):
                with patch(
                    "cyt_client.cli.post_hook_inject",
                    return_value=(200, b'{"hookSpecificOutput":{"additionalContext":"x"}}'),
                ):
                    from cyt_client.cli import main

                    with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                        main(["--rule", ".cursor/rules/cyt-indexer.mdc"])

            captured = capsys.readouterr()
            assert captured.out == '{"hookSpecificOutput":{"additionalContext":"x"}}'
            assert not custom_rules.is_file()
    finally:
        reset_rules_file_rel_path()


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
    assert enriched["cyt_hook_payload"]["hook_event_name"] == "beforeSubmitPrompt"
    assert "cyt_agent" not in enriched["cyt_hook_payload"]
    assert enriched["cyt_agent"] == "cursor"
    assert "cyt_skills" in enriched


def test_enrich_hook_payload_adds_cyt_cwd_from_cwd_only() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "cwd": "/tmp/isolated-project",
        },
    ).encode()
    enriched = json.loads(enrich_hook_payload(raw))
    assert enriched["cyt"]["cwd"] == "/tmp/isolated-project"
    assert "workspace_roots" not in enriched
    assert "cyt" not in enriched["cyt_hook_payload"]


def test_enrich_hook_payload_adds_cyt_cwd_from_workspace_roots() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "workspace_roots": ["/tmp/project", "/tmp/other"],
        },
    ).encode()
    enriched = json.loads(enrich_hook_payload(raw))
    assert enriched["cyt"]["cwd"] == "/tmp/project"
    assert enriched["workspace_roots"] == ["/tmp/project", "/tmp/other"]
    assert enriched["cyt_hook_payload"]["workspace_roots"] == ["/tmp/project", "/tmp/other"]


def test_enrich_hook_payload_skips_cyt_when_no_workspace_path() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
        },
    ).encode()
    enriched = json.loads(enrich_hook_payload(raw))
    assert "cyt" not in enriched


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
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        payload = json.dumps(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "prompt": "hello",
                "conversation_id": "conv-1",
                "workspace_roots": [str(workspace)],
            },
        ).encode()
        with patch("cyt_client.cli.resolve_hook_url", return_value=None):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        assert captured.err == ""


def test_cli_cursor_invalid_workspace_silent_continue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": ["/path/to/project"],
        },
    ).encode()
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject") as post:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main()

    post.assert_not_called()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"continue": True}
    assert captured.err == ""


def test_cli_cursor_invalid_workspace_verbose_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": ["/path/to/project"],
        },
    ).encode()
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject") as post:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main(["--verbose"])

    post.assert_not_called()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"continue": True}
    assert "invalid workspace root: /path/to/project" in captured.err


def test_is_valid_workspace_root() -> None:
    from cyt_client.rules_file import is_valid_workspace_root

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        assert is_valid_workspace_root(workspace)
        assert not is_valid_workspace_root(workspace / "missing")
        assert not is_valid_workspace_root(Path("/path/to/project"))


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


def test_enrich_hook_payload_adds_cyt_rules_injection_from_cursor_rules_file() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        injection = "<agent-tools>\n<tool name='mcp__seen__tool'>\n{'input_schema':{}}\n</tool>\n</agent-tools>"
        rules_file_path(workspace).parent.mkdir(parents=True, exist_ok=True)
        rules_file_path(workspace).write_text(build_rules_mdc(injection), encoding="utf-8")
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "cwd": str(workspace),
        }
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert enriched["cyt_rules_injection"] == injection


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


def test_cursor_rules_file_enabled_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from cyt_client.config import skills_hook_cursor_rule_file_enabled
    from cyt_client.rules_file import cursor_rules_file_enabled, sync_cursor_rules_file

    monkeypatch.delenv("CYT_CURSOR_RULES_FILE", raising=False)
    assert skills_hook_cursor_rule_file_enabled() is True
    assert cursor_rules_file_enabled() is True

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.yaml"
        config_path.write_text(
            "skills:\n  hook:\n    cursor_rule_file:\n      enabled: false\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp)
        assert skills_hook_cursor_rule_file_enabled() is False
        assert cursor_rules_file_enabled() is False

        workspace = Path(tmp) / "repo"
        workspace.mkdir()
        assert sync_cursor_rules_file(workspace, "<agent-tools>demo</agent-tools>") is False


def test_cursor_rules_file_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from cyt_client.rules_file import cursor_rules_file_enabled

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.yaml"
        config_path.write_text(
            "skills:\n  hook:\n    cursor_rule_file:\n      enabled: false\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp)
        monkeypatch.setenv("CYT_CURSOR_RULES_FILE", "1")
        assert cursor_rules_file_enabled() is True


def test_sync_cursor_rules_file_skips_unchanged_content() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path, sync_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        injection = "<agent-tools>demo</agent-tools>"
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_rules_mdc(injection), encoding="utf-8")

        assert sync_cursor_rules_file(workspace, injection) is False


def test_merge_rules_injection_keeps_prior_tools_when_delta_is_skills_only() -> None:
    from cyt_client.rules_file import merge_rules_injection

    prior = "<agent-tools><tool name='mcp__a__grep'>demo</tool></agent-tools>"
    delta = (
        "Based on the user query added chunks.\n\n"
        "<agent-skills><skill name='lean-ctx'>ctx_edit</skill></agent-skills>"
    )
    merged = merge_rules_injection(prior, delta)
    assert "<agent-tools>" in merged
    assert "<agent-skills>" in merged
    assert merged.index("<agent-skills>") < merged.index("<agent-tools>")


def test_merge_rules_injection_keeps_prior_skills_when_delta_is_tools_only() -> None:
    from cyt_client.rules_file import merge_rules_injection

    prior = "<agent-skills><skill name='lean-ctx'>ctx_edit</skill></agent-skills>"
    delta = "<agent-tools><tool name='mcp__a__grep'>demo</tool></agent-tools>"
    merged = merge_rules_injection(prior, delta)
    assert "<agent-skills>" in merged
    assert "<agent-tools>" in merged


def test_merge_rules_injection_updates_one_inner_source_section() -> None:
    from cyt_client.rules_file import merge_rules_injection

    prior = (
        "<agent-tools description='x'>"
        "<executor><tool name='old'>old</tool></executor>"
        "<definitions><tool name='defs'>defs</tool></definitions>"
        "</agent-tools>"
    )
    delta = (
        "<agent-tools description='x'>"
        "<executor><tool name='new'>new</tool></executor>"
        "</agent-tools>"
    )
    merged = merge_rules_injection(prior, delta)
    assert "name='new'" in merged
    assert "name='old'" not in merged
    assert "name='defs'" in merged


def test_sync_cursor_rules_file_merges_delta_with_prior_sections() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path, sync_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            build_rules_mdc("<agent-skills><skill name='lean-ctx'>ctx</skill></agent-skills>"),
            encoding="utf-8",
        )

        assert (
            sync_cursor_rules_file(
                workspace,
                "<agent-tools><tool name='mcp__a__grep'>demo</tool></agent-tools>",
                merge_sections=True,
            )
            is True
        )
        body = path.read_text(encoding="utf-8")
        assert "<agent-skills>" in body
        assert "<agent-tools>" in body


def test_sync_cursor_rules_file_replaces_on_single_domain_sync() -> None:
    from cyt_client.rules_file import build_rules_mdc, rules_file_path, sync_cursor_rules_file

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            build_rules_mdc("<agent-skills><skill name='lean-ctx'>ctx</skill></agent-skills>"),
            encoding="utf-8",
        )

        assert (
            sync_cursor_rules_file(
                workspace,
                "<agent-tools><tool name='mcp__a__grep'>demo</tool></agent-tools>",
                merge_sections=False,
            )
            is True
        )
        body = path.read_text(encoding="utf-8")
        assert "<agent-tools>" in body
        assert "<agent-skills>" not in body


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


def test_consume_cursor_rules_injection_reads_and_deletes() -> None:
    from cyt_client.rules_file import (
        build_rules_mdc,
        consume_cursor_rules_injection,
        rules_file_path,
    )

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        injection = "<agent-skills>prior</agent-skills>"
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_rules_mdc(injection), encoding="utf-8")

        assert consume_cursor_rules_injection(workspace) == injection
        assert not path.is_file()


def test_consume_cursor_rules_injection_noop_when_absent() -> None:
    from cyt_client.rules_file import consume_cursor_rules_injection

    with tempfile.TemporaryDirectory() as tmp:
        assert consume_cursor_rules_injection(Path(tmp)) == ""


def test_cli_before_submit_deletes_stale_rules_file_before_inject(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("stale content", encoding="utf-8")
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
                        "additionalContext": "<agent-skills>fresh</agent-skills>",
                    },
                },
            ).encode()

            def _post_after_rules_consumed(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
                assert rules_path.is_file()
                return 200, inject_response

            with patch(
                "cyt_client.cli.post_hook_inject",
                side_effect=_post_after_rules_consumed,
            ) as post:
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main()

        post.assert_called_once()
        sent = json.loads(post.call_args.args[1])
        assert sent["cyt_rules_injection"] == "stale content"
        assert rules_path.is_file()
        assert "fresh" in rules_path.read_text(encoding="utf-8")
        assert "stale content" not in rules_path.read_text(encoding="utf-8")
        assert json.loads(capsys.readouterr().out) == {
            "continue": True,
            "additional_context": "<agent-skills>fresh</agent-skills>",
        }


def test_cli_before_submit_deletes_rules_file_on_empty_injection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("existing pruned tools", encoding="utf-8")
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
                        "additionalContext": "",
                    },
                },
            ).encode()

            def _post_after_rules_consumed(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
                assert rules_path.is_file()
                return 200, inject_response

            with patch(
                "cyt_client.cli.post_hook_inject",
                side_effect=_post_after_rules_consumed,
            ):
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main(["--verbose"])

        captured = capsys.readouterr()
        assert rules_path.is_file()
        assert json.loads(captured.out) == {"continue": True}
        assert "pre-exposure skip" in captured.err


def test_cli_before_submit_fresh_skips_prior_rules_injection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("existing pruned tools", encoding="utf-8")
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
                        "additionalContext": "<agent-tools>fresh</agent-tools>",
                    },
                },
            ).encode()

            with patch(
                "cyt_client.cli.post_hook_inject",
                return_value=(200, inject_response),
            ) as post:
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main(["--fresh"])

        sent = json.loads(post.call_args.args[1])
        assert "cyt_rules_injection" not in sent
        assert (
            json.loads(capsys.readouterr().out)["additional_context"]
            == "<agent-tools>fresh</agent-tools>"
        )


def test_cli_before_submit_preserves_rules_file_when_hook_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        rules_path.parent.mkdir(parents=True)
        rules_path.write_text("prior pruned context", encoding="utf-8")
        payload = {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "hello",
            "conversation_id": "conv-1",
            "workspace_roots": [str(workspace)],
        }
        with patch("cyt_client.cli.resolve_hook_url", return_value=None):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                main(["--verbose"])

        assert rules_path.is_file()
        assert rules_path.read_text(encoding="utf-8") == "prior pruned context"
        assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_cli_session_end_resets_rules_file_to_placeholder_without_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.rules_file import build_rules_mdc_placeholder

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
        assert rules_path.is_file()
        assert rules_path.read_text(encoding="utf-8") == build_rules_mdc_placeholder()
        assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_cli_session_start_resets_rules_file_to_placeholder_without_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.rules_file import build_rules_mdc_placeholder

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
        assert rules_path.is_file()
        assert rules_path.read_text(encoding="utf-8") == build_rules_mdc_placeholder()
        assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_reset_cursor_rules_file_to_placeholder_creates_file_when_absent() -> None:
    from cyt_client.rules_file import (
        build_rules_mdc_placeholder,
        reset_cursor_rules_file_to_placeholder,
        rules_file_path,
    )

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        path = rules_file_path(workspace)
        assert reset_cursor_rules_file_to_placeholder(workspace) is True
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == build_rules_mdc_placeholder()
        assert reset_cursor_rules_file_to_placeholder(workspace) is False


def test_cli_passes_debug_header_to_hook_server() -> None:
    payload = (
        b'{"hook_event_name":"UserPromptSubmit","prompt":"hello","cwd":"/tmp/isolated-project"}'
    )
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject", return_value=(200, b"")) as post:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                main(["--debug"])

    post.assert_called_once()
    assert post.call_args.kwargs["debug"] is True


def test_hook_stdout_bytes_for_agent_strips_session_fields() -> None:
    from cyt_client.rules_file import hook_stdout_bytes_for_agent

    body = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "ctx",
            },
            "cytAgent": "cursor",
            "cytSessionLog": [{"kind": "tool", "key": "tool:Shell"}],
            "cytPhaseTiming": {"total_ms": 10, "phases": []},
        },
    ).encode()
    stripped = hook_stdout_bytes_for_agent(body)
    data = json.loads(stripped)
    assert "cytAgent" not in data
    assert "cytSessionLog" not in data
    assert "cytPhaseTiming" not in data
    assert data["hookSpecificOutput"]["additionalContext"] == "ctx"


def test_codex_session_end_runs_cleanup_without_http() -> None:
    from cyt_client.cli import main

    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": "codex-sess",
        "cwd": "/tmp/project",
    }
    raw = json.dumps(payload).encode()

    def fake_read() -> bytes:
        return raw

    with (
        patch("cyt_client.cli.resolve_hook_url") as resolve,
        patch(
            "cyt_client.cli.cleanup_stale_session_logs",
            return_value=[],
        ) as cleanup,
        patch("sys.stdin.buffer.read", fake_read),
    ):
        resolve.return_value = None
        main([])
    cleanup.assert_called_once()
