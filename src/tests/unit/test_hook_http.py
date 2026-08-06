"""Tests for hook HTTP server route and shared handler."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from cyt.proxy.reverse import create_app
from cyt.pruners.remote import PrunerSettingsCache, RemotePruningSettings
from cyt.skills.cli import HookRunResult, run_hook_payload


@pytest.fixture
async def hook_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        routes={},
        config={
            "skills": {"enabled": False},
            "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
        },
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_health_includes_hook_flag(hook_client: httpx.AsyncClient) -> None:
    response = await hook_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["hook"] is True


@pytest.mark.asyncio
async def test_hook_inject_returns_formatted_stdout(hook_client: httpx.AsyncClient) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    result = HookRunResult(
        stdout_text=json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "injected",
                },
            },
        ),
        outcome="user_prompt_injected",
        details={},
    )
    with patch("cyt.hook.http_server.run_hook_payload", return_value=result):
        response = await hook_client.post("/hook/inject", json=payload)

    assert response.status_code == 200
    assert "injected" in response.text


@pytest.mark.asyncio
async def test_hook_inject_honors_debug_header(hook_client: httpx.AsyncClient) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    result = HookRunResult(stdout_text="", outcome="noop", details={})
    with patch("cyt.hook.http_server.run_hook_payload", return_value=result) as run_hook:
        response = await hook_client.post(
            "/hook/inject",
            json=payload,
            headers={"X-CYT-Hook-Debug": "1"},
        )

    assert response.status_code == 200
    assert run_hook.call_args.kwargs["debug"] is True
    assert run_hook.call_args.kwargs["request_payload"] == payload


@pytest.mark.asyncio
async def test_hook_connect_verify_only_response() -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello", "session_id": "sess-1"}
    verify_config = {
        "hallucination_gate": {"enabled": True},
        "skills": {"enabled": False},
        "pruning": {
            "tools": {"enabled": False, "hook": {"tools_from": ["cyt_mcp"]}},
            "inject_via": {"cursor": "hook"},
        },
    }
    app = create_app(routes={}, config=verify_config)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as hook_client:
        with patch(
            "cyt.hook.http_server._run_verify_session_log",
            return_value=[{"kind": "session_state", "key": "session_state:inject"}],
        ):
            response = await hook_client.post("/hook/connect", json=payload)

    assert response.status_code == 200
    body = json.loads(response.text)
    assert body.get("verify-only") is True
    assert body.get("hookSpecificOutput") == {}
    assert "cytSessionLog" in body


@pytest.mark.asyncio
async def test_hook_inject_empty_body(hook_client: httpx.AsyncClient) -> None:
    response = await hook_client.post("/hook/inject", content=b"")
    assert response.status_code == 200
    assert response.text == ""


@pytest.mark.asyncio
async def test_hook_inject_passes_app_state_pruner_settings() -> None:
    cached = RemotePruningSettings(
        "test-model",
        "startup-key",
        "https://example.com",
        "test",
        "example.com",
    )
    pruner_settings = PrunerSettingsCache(llm=cached)
    app = create_app(
        routes={},
        config={
            "skills": {"enabled": False},
            "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}},
        },
        pruner_settings=pruner_settings,
    )
    app.state.pruner_settings = pruner_settings
    transport = httpx.ASGITransport(app=app)
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    result = HookRunResult(stdout_text="", outcome="noop", details={})

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        with patch("cyt.hook.http_server.run_hook_payload", return_value=result) as run_hook:
            response = await client.post("/hook/inject", json=payload)

    assert response.status_code == 200
    assert run_hook.call_args.kwargs["pruner_settings"] is pruner_settings


def test_run_hook_payload_defers_cache_when_not_provided() -> None:
    config: dict[str, Any] = {
        "skills": {"enabled": False},
        "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
    }
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    with patch("cyt.launch.secrets.build_pruner_settings_cache") as build:
        with patch("cyt.skills.cli._dispatch_hook_event") as dispatch:
            dispatch.return_value = ("skipped_inject_via_proxy", {}, "")
            run_hook_payload(payload, config)
            build.assert_not_called()
            assert dispatch.call_args.kwargs["pruner_settings"] is None


def test_run_hook_payload_reuses_provided_cache() -> None:
    config: dict[str, Any] = {
        "skills": {"enabled": False},
        "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
    }
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    provided = PrunerSettingsCache()
    with patch("cyt.launch.secrets.build_pruner_settings_cache") as build:
        with patch("cyt.skills.cli._dispatch_hook_event") as dispatch:
            dispatch.return_value = ("skipped_inject_via_proxy", {}, "")
            run_hook_payload(payload, config, pruner_settings=provided)
            build.assert_not_called()
            assert dispatch.call_args.kwargs["pruner_settings"] is provided


def test_run_hook_payload_does_not_print(capsys: pytest.CaptureFixture[str]) -> None:
    config: dict[str, Any] = {
        "skills": {"enabled": False},
        "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}},
    }
    payload = {"hook_event_name": "SessionStart", "session_id": "sess-test"}
    with patch("cyt.hook.daemon.daemon_start") as daemon_start:
        from cyt.hook.daemon import HookDaemonStartResult

        daemon_start.return_value = HookDaemonStartResult(
            outcome="reused",
            port=8834,
            hook_url="http://127.0.0.1:8834/hook/inject",
            pid=None,
            reused=True,
        )
        result = run_hook_payload(payload, config)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result.stdout_text == ""
    assert result.outcome == "session_start_daemon_reused"


def test_run_hook_payload_disables_transcript_file_read_by_default() -> None:
    config: dict[str, Any] = {
        "skills": {"enabled": False},
        "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
    }
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    with patch("cyt.skills.cli._dispatch_hook_event") as dispatch:
        dispatch.return_value = ("skipped_inject_via_proxy", {}, "")
        run_hook_payload(payload, config)
        assert dispatch.call_args.kwargs["allow_transcript_file_read"] is False
