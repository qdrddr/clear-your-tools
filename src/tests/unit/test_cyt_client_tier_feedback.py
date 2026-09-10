"""Tests for cyt-client tier feedback HTTP notify."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch
from urllib.request import Request

from cyt_client.tier_feedback import notify_tool_used_feedback


def test_notify_tool_used_feedback_posts_to_daemon(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    payload = {"workspace_roots": [str(tmp_path)]}
    captured: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(cast(bytes, request.data or b"").decode())
        captured["timeout"] = timeout
        return FakeResponse()

    with (
        patch(
            "cyt_client.tier_feedback.resolve_hook_url",
            return_value="http://127.0.0.1:9999/hook/connect",
        ),
        patch("cyt_client.tier_feedback.urlopen", side_effect=fake_urlopen),
    ):
        notify_tool_used_feedback(
            payload,
            tool_name="search",
            catalog="cyt_mcp",
            args={"query": "x"},
        )

    assert captured["url"] == "http://127.0.0.1:9999/hook/tier/feedback"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["event"] == "tool_used"
    assert body["tool_name"] == "search"
    assert body["catalog"] == "cyt_mcp"


def test_notify_skill_used_feedback_posts_to_daemon(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# Skill\n", encoding="utf-8")
    payload = {"workspace_roots": [str(tmp_path)]}
    captured: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(cast(bytes, request.data or b"").decode())
        captured["timeout"] = timeout
        return FakeResponse()

    with (
        patch(
            "cyt_client.tier_feedback.resolve_hook_url",
            return_value="http://127.0.0.1:9999/hook/connect",
        ),
        patch("cyt_client.tier_feedback.urlopen", side_effect=fake_urlopen),
    ):
        from cyt_client.tier_feedback import notify_skill_used_feedback

        notify_skill_used_feedback(
            payload,
            entity_id=str(skill_path.resolve()),
        )

    assert captured["url"] == "http://127.0.0.1:9999/hook/tier/feedback"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["event"] == "skill_used"
    assert body["entity_id"] == str(skill_path.resolve())
    assert "without_injection" not in body


def test_notify_skill_used_feedback_skips_without_workspace() -> None:
    with patch("cyt_client.tier_feedback.urlopen") as urlopen_mock:
        from cyt_client.tier_feedback import notify_skill_used_feedback

        notify_skill_used_feedback({}, entity_id="/tmp/skill.md")
    urlopen_mock.assert_not_called()

    with patch("cyt_client.tier_feedback.urlopen") as urlopen_mock:
        notify_tool_used_feedback({}, tool_name="search", catalog="cyt_mcp")
    urlopen_mock.assert_not_called()
