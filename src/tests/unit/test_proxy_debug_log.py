"""Tests for proxy --debug log append behavior."""

from __future__ import annotations

import json
from pathlib import Path

from cyt.proxy.reverse import body_for_original_snapshot
from cyt.proxy.transport import (
    append_debug_log_block,
    append_debug_snapshot,
    append_original_debug_snapshot,
    reverse_debug_log_path,
    reverse_debug_original_log_path,
    reverse_debug_proxy_log_path,
)


def test_debug_log_appends_all_requests_without_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "openai.json"
    proxy_log_path = tmp_path / "openai-proxy.log"
    append_debug_log_block(proxy_log_path, label="pruning", content="request-1\n")
    append_debug_snapshot(log_path, {"method": "POST", "path": "/v1/responses"})
    append_debug_log_block(proxy_log_path, label="pruning", content="request-2\n")
    append_debug_snapshot(log_path, {"method": "POST", "path": "/v1/responses"})

    request_entries = json.loads(log_path.read_text(encoding="utf-8"))
    proxy_text = proxy_log_path.read_text(encoding="utf-8")
    assert isinstance(request_entries, list)
    assert len(request_entries) == 2
    assert request_entries[0]["path"] == "/v1/responses"
    assert request_entries[1]["path"] == "/v1/responses"
    assert proxy_text.count("pruning") == 2
    assert "request-1" in proxy_text
    assert "request-2" in proxy_text
    assert proxy_text.index("request-1") < proxy_text.index("request-2")


def test_reverse_debug_original_log_path(tmp_path: Path) -> None:
    assert reverse_debug_original_log_path("anthropic", debug_log_dir=tmp_path) == (
        tmp_path / "anthropic-original.json"
    )
    assert reverse_debug_log_path("anthropic", debug_log_dir=tmp_path) == (
        tmp_path / "anthropic.json"
    )
    assert reverse_debug_proxy_log_path("anthropic", debug_log_dir=tmp_path) == (
        tmp_path / "anthropic-proxy.log"
    )


def test_body_for_original_snapshot_parses_json_content_type() -> None:
    body = b'{"tools":[{"name":"a"}],"model":"x"}'
    assert body_for_original_snapshot(body, "application/json") == {
        "tools": [{"name": "a"}],
        "model": "x",
    }


def test_body_for_original_snapshot_invalid_json_falls_back_to_text() -> None:
    body = b"{not valid json"
    assert body_for_original_snapshot(body, "application/json") == "{not valid json"


def test_original_debug_log_uses_separate_file(tmp_path: Path) -> None:
    original_path = reverse_debug_original_log_path("openai", debug_log_dir=tmp_path)
    mutated_path = reverse_debug_log_path("openai", debug_log_dir=tmp_path)
    body = {"tools": [], "model": "gpt-test"}
    payload = {
        "debug_request_seq": 1,
        "body": body,
        "path": "/openai/v1/messages",
        "target_url": "https://api.openai.com/v1/responses",
        "timestamp": "2026-06-08T12:00:00+00:00",
    }
    append_original_debug_snapshot(original_path, payload)
    append_debug_snapshot(mutated_path, payload)

    original_entries = json.loads(original_path.read_text(encoding="utf-8"))
    mutated_entries = json.loads(mutated_path.read_text(encoding="utf-8"))
    assert len(original_entries) == 1
    assert len(mutated_entries) == 1
    assert original_entries[0]["body"] == body
    assert mutated_entries[0]["body"] == body
    assert original_entries[0]["timestamp"] == "2026-06-08T12:00:00+00:00"
