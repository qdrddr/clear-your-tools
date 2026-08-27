"""Gherkin steps for pre-exposure and compaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.common.agents import AgentName
from cyt.injection.pre_exposed import filter_pre_exposed_native_tools
from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.pre_exposure_pipeline import gate_and_filter_tools
from cyt.proxy.session_log import persist_proxy_session_log_entries
from cyt_client.cli import _handle_non_cursor_hook, _handle_pre_compact
from cyt_client.rules_file import read_cursor_rules_injection
from cyt_client.sessions import read_session_log_file, sessions_dir_for_agent
from cyt_client.transcript import enrich_hook_payload
from tests.conftest import isolate_user_home
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "pre_exposure_compaction.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given(parsers.parse("agent {agent}"))
def given_agent(agent: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = cast(AgentName, agent)


@given("a preCompact hook payload with session id compact-sess")
def given_precompact_payload(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    gherkin_context.tmp_path = tmp_path
    gherkin_context.payload = {
        "hook_event_name": "preCompact",
        "session_id": "compact-sess",
        "cyt_agent": "claude",
        "trigger": "auto",
    }


@given("inject_via proxy for claude")
def given_inject_via_proxy(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".config" / "cyt"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "pruning:\n  inject_via:\n    claude: proxy\n",
        encoding="utf-8",
    )
    isolate_user_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    gherkin_context.config["inject_via_proxy"] = True


@when("preCompact hook is handled")
def when_precompact_hook(gherkin_context: GherkinContext) -> None:
    _handle_pre_compact(gherkin_context.payload, cursor_output=False)


@then("session log should contain compaction entry")
def then_compaction_entry(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = sessions_dir_for_agent("claude") / "compact-sess.jsonl"
    _agent, entries = read_session_log_file(log_path)
    assert any(entry.get("kind") == "compaction" for entry in entries)


@then("session log path should be under agent home")
def then_agent_home_path(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = sessions_dir_for_agent("claude") / "compact-sess.jsonl"
    assert Path(log_path).as_posix().startswith((tmp_path / ".claude").as_posix())


@given("a Cursor workspace with injected rules file content")
def given_rules_file(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    workspace = tmp_path / "project"
    rules = workspace / ".cursor" / "rules"
    rules.mkdir(parents=True)
    rules_file = rules / "cyt-injection.mdc"
    rules_file.write_text("---\nalwaysApply: true\n---\n\ninjected tools", encoding="utf-8")
    gherkin_context.payload = {
        "hook_event_name": "preCompact",
        "session_id": "rules-sess",
        "cwd": str(workspace),
        "workspace_roots": [str(workspace)],
    }


@when("preCompact hook is handled for that workspace")
def when_precompact_workspace(gherkin_context: GherkinContext) -> None:
    _handle_pre_compact(gherkin_context.payload, cursor_output=False)


@then("the Cursor rules file should be a session lifecycle placeholder")
def then_rules_placeholder(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    workspace = Path(gherkin_context.payload["cwd"])
    body = read_cursor_rules_injection(workspace)
    assert "Re-read this file" in body


@given("a session log with pre and post compaction tool entries")
def given_pre_post_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    log_path = tmp_path / ".cursor" / "cyt" / "sessions" / "slice-sess.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "meta", "agent": "cursor"}),
        json.dumps({"kind": "tool", "key": "tool:old", "name": "old"}),
        json.dumps({"kind": "compaction", "key": "compaction", "payload": {}}),
        json.dumps({"kind": "tool", "key": "tool:new", "name": "new"}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@when("hook payload is enriched with cyt_session_log")
def when_enrich_payload(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "slice-sess",
        "cyt_agent": "cursor",
        "prompt": "hello",
        "cwd": str(tmp_path),
    }
    from cyt_client.sessions import session_log_path

    assert session_log_path(payload) is not None
    enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
    gherkin_context.payload = enriched


@then("attached cyt_session_log should exclude pre-compaction tools")
def then_sliced_attach(gherkin_context: GherkinContext) -> None:
    attached = gherkin_context.payload.get("cyt_session_log") or []
    keys = [entry.get("key") for entry in attached]
    assert "tool:new" in keys
    assert "tool:old" not in keys


@given("a post-compaction session log without Type-1 demo_tool")
def given_post_compaction_no_tool(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "entries": [
            {"kind": "compaction", "key": "compaction", "payload": {}},
        ],
        "tool": {"name": "demo_tool", "tool_name": "demo_tool", "input_schema": {}},
        "config": {"pruning": {"tools": {"enabled": True}}},
    }


@when("hook gates demo_tool against post-compaction corpus")
def when_hook_gate_tool(gherkin_context: GherkinContext) -> None:
    ctx = PreExposureContext.from_entries(
        payload_text="",
        entries=gherkin_context.payload["entries"],
    )
    gated, _logs, _ = gate_and_filter_tools(
        [gherkin_context.payload["tool"]],
        config=gherkin_context.payload["config"],
        ctx=ctx,
    )
    gherkin_context.payload["gated"] = gated


@then("hook should not skip demo_tool injection")
def then_tool_not_skipped(gherkin_context: GherkinContext) -> None:
    assert len(gherkin_context.payload["gated"]) == 1


@given("a post-compaction session log without skill demo-skill")
def given_post_compaction_no_skill(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "entries": [{"kind": "compaction", "key": "compaction", "payload": {}}],
    }


@when("hook gates demo-skill against post-compaction corpus")
def when_hook_gate_skill(gherkin_context: GherkinContext) -> None:
    from cyt.injection.pre_exposure_pipeline import gate_and_filter_skills
    from cyt.skills.search import MatchedSkill

    ctx = PreExposureContext.from_entries(
        payload_text="",
        entries=gherkin_context.payload["entries"],
    )
    match = MatchedSkill(
        doc_id="demo-skill",
        file_path="/tmp/demo/SKILL.md",
        markdown="---\nname: demo\n---\nbody",
        name="demo-skill",
        score=1.0,
        token_count=10,
        command=None,
    )
    gated, _ = gate_and_filter_skills([match], config={"skills": {"enabled": True}}, ctx=ctx)
    gherkin_context.payload["gated"] = gated


@then("hook should not skip demo-skill injection")
def then_skill_not_skipped(gherkin_context: GherkinContext) -> None:
    assert len(gherkin_context.payload["gated"]) == 1


@given("proxy body containing tool fragment for demo_tool")
def given_proxy_body_tool(gherkin_context: GherkinContext) -> None:
    from cyt.tools.inject import format_tool_item

    tool = {"name": "demo_tool", "tool_name": "demo_tool", "input_schema": {}}
    fragment = format_tool_item(tool, include_tool_description=True)
    gherkin_context.payload = {
        "payload_text": fragment,
        "tool": tool,
        "config": {"pruning": {"tools": {"enabled": True}}},
    }


@when("proxy payload gate filters demo_tool")
def when_proxy_payload_gate(gherkin_context: GherkinContext) -> None:
    from cyt.injection.pre_exposed import filter_pre_exposed_tools

    ctx = PreExposureContext.from_entries(
        payload_text=gherkin_context.payload["payload_text"],
        entries=[],
    )
    gated = filter_pre_exposed_tools(
        [gherkin_context.payload["tool"]],
        ctx.payload_text,
    )
    gherkin_context.payload["gated"] = gated


@then("proxy should skip demo_tool for payload gate")
def then_proxy_skip_payload(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["gated"] == []


@given("proxy session index with Type-1 demo_tool full entry")
def given_proxy_session_tool(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "entries": [
            {
                "kind": "tool",
                "key": "tool:demo_tool",
                "hash": "hash-v2",
                "full": True,
                "name": "demo_tool",
            },
        ],
        "tool": {"name": "demo_tool", "tool_name": "demo_tool", "input_schema": {}},
        "config": {"pruning": {"tools": {"enabled": True}}},
    }


@when("proxy session gate filters demo_tool")
def when_proxy_session_gate(gherkin_context: GherkinContext) -> None:
    ctx = PreExposureContext.from_entries(
        payload_text="",
        entries=gherkin_context.payload["entries"],
    )
    gated, _logs, _ = gate_and_filter_tools(
        [gherkin_context.payload["tool"]],
        config=gherkin_context.payload["config"],
        ctx=ctx,
    )
    gherkin_context.payload["gated"] = gated


@then("proxy should skip demo_tool for session gate")
def then_proxy_skip_session(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["gated"] == []


@given("proxy session index with Type-1 native_tool full entry")
def given_native_session_tool(gherkin_context: GherkinContext) -> None:
    given_proxy_session_tool(gherkin_context)
    gherkin_context.payload["tool"]["name"] = "native_tool"
    gherkin_context.payload["entries"][0]["key"] = "tool:native_tool"
    gherkin_context.payload["entries"][0]["name"] = "native_tool"


@when("native proxy tools gate filters native_tool")
def when_native_gate(gherkin_context: GherkinContext) -> None:
    ctx = PreExposureContext.from_entries(
        payload_text="",
        entries=gherkin_context.payload["entries"],
    )
    gated = filter_pre_exposed_native_tools(
        [gherkin_context.payload["tool"]],
        ctx,
        config=gherkin_context.payload["config"],
    )
    gherkin_context.payload["gated"] = gated


@then("native proxy should omit native_tool")
def then_native_omit(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["gated"] == []


@given("proxy payload corpus containing mcp__demo__run")
def given_native_payload_corpus(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "payload_text": "prior turn used mcp__demo__run successfully",
        "tool": {"name": "mcp__demo__run", "tool_name": "run", "input_schema": {}},
        "config": {"pruning": {"tools": {"enabled": True}}},
        "entries": [],
    }


@when("native proxy tools gate filters mcp__demo__run")
def when_native_payload_gate(gherkin_context: GherkinContext) -> None:
    ctx = PreExposureContext.from_entries(
        payload_text=gherkin_context.payload["payload_text"],
        entries=gherkin_context.payload["entries"],
    )
    gated = filter_pre_exposed_native_tools(
        [gherkin_context.payload["tool"]],
        ctx,
        config=gherkin_context.payload["config"],
    )
    gherkin_context.payload["gated"] = gated


@then("native proxy should omit mcp__demo__run")
def then_native_payload_omit(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["gated"] == []


@given("proxy inject produced tool log entries and catalog entry")
def given_proxy_persist_entries(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    gherkin_context.payload = {
        "entries": [
            {"kind": "tool", "key": "tool:demo", "hash": "abc", "name": "demo"},
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "cat-hash",
                "tools": [{"name": "demo", "input_schema": {}}],
            },
        ],
        "config": {"pruning": {"inject_via": {"claude": "proxy"}}},
    }


@when("proxy session log writer persists inject results")
def when_proxy_persist(gherkin_context: GherkinContext) -> None:
    persist_proxy_session_log_entries(
        agent="claude",
        session_id="proxy-sess",
        entries=gherkin_context.payload["entries"],
        config=gherkin_context.payload["config"],
    )


@then("session log should contain persisted tool and catalog lines")
def then_proxy_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    log_path = sessions_dir_for_agent("claude") / "proxy-sess.jsonl"
    _agent, entries = read_session_log_file(log_path)
    kinds = {entry.get("kind") for entry in entries}
    assert "tool" in kinds
    assert "tool_catalog" in kinds


@given("a UserPromptSubmit hook payload")
def given_user_prompt(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello",
        "session_id": "sess-1",
        "cyt_agent": "claude",
    }


@when("non-cursor hook inject is attempted")
def when_non_cursor_inject(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".config" / "cyt"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "pruning:\n  inject_via:\n    claude: proxy\n",
        encoding="utf-8",
    )
    isolate_user_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    with (
        patch("cyt_client.cli.inject_via_for_agent", return_value="proxy"),
        patch(
            "cyt_client.cli.resolve_hook_url",
            return_value="http://127.0.0.1:9999",
        ),
        patch(
            "cyt_client.cli.verify_only_mode",
            return_value=False,
        ),
        patch(
            "cyt_client.cli.post_hook_inject",
            return_value=(200, b""),
        ) as post_mock,
    ):
        _handle_non_cursor_hook(
            json.dumps(gherkin_context.payload).encode(),
            gherkin_context.payload,
        )
        gherkin_context.payload["post_called"] = post_mock.called


@then("hook daemon should not be called")
def then_no_daemon(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("post_called") is False


@given("a session log with pre and post compaction catalog hashes")
def given_pre_post_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_user_home(monkeypatch, tmp_path)
    log_path = sessions_dir_for_agent("claude") / "hash-sess.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "meta", "agent": "claude"}),
        json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "old",
                "tools": [{"name": "old", "input_schema": {}}],
            },
        ),
        json.dumps({"kind": "compaction", "key": "compaction", "payload": {}}),
        json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "new",
                "tools": [{"name": "new", "input_schema": {}}],
            },
        ),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@when("read_tool_catalog_hashes is called post-compaction")
def when_read_hashes(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.sessions import read_tool_catalog_hashes

    isolate_user_home(monkeypatch, tmp_path)
    log_path = sessions_dir_for_agent("claude") / "hash-sess.jsonl"
    gherkin_context.payload["hashes"] = read_tool_catalog_hashes(log_path)


@then("catalog hashes should reflect post-compaction only")
def then_post_compaction_hashes(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["hashes"]["tool_catalog:cyt_mcp"] == "new"


@given("prevent-hallucination hook overlay config")
def given_hallucination_overlay(gherkin_context: GherkinContext) -> None:
    gherkin_context.config = {
        "skills": {"enabled": False},
        "pruning": {"tools": {"enabled": False}, "inject_via": {"claude": "hook"}},
        "hallucination_gate": {"enabled": True},
    }


@then("verify-only mode should be enabled for hook agent")
def then_verify_only(gherkin_context: GherkinContext) -> None:
    from cyt.config import verify_only_mode

    assert verify_only_mode(gherkin_context.config) is True
