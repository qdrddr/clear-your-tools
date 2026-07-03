"""Tests for hook HTTP server route and shared handler."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from cyt.proxy.reverse import create_app
from cyt.skills.cli import HookRunResult, run_hook_payload


@pytest.fixture
async def hook_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        routes={},
        config={"skills": {"enabled": False}, "pruning": {"inject_via": "proxy"}},
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
async def test_hook_inject_empty_body(hook_client: httpx.AsyncClient) -> None:
    response = await hook_client.post("/hook/inject", content=b"")
    assert response.status_code == 200
    assert response.text == ""


def test_run_hook_payload_does_not_print(capsys: pytest.CaptureFixture[str]) -> None:
    config: dict[str, Any] = {
        "skills": {"enabled": False},
        "pruning": {"inject_via": "hook"},
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
