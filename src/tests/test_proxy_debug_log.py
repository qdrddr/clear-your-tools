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
)


def test_debug_log_appends_all_requests_without_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "openai.log"
    append_debug_log_block(log_path, label="pruning", content="request-1\n")
    append_debug_snapshot(log_path, {"method": "POST", "path": "/v1/responses"})
    append_debug_log_block(log_path, label="pruning", content="request-2\n")
    append_debug_snapshot(log_path, {"method": "POST", "path": "/v1/responses"})

    text = log_path.read_text(encoding="utf-8")
    assert text.count("pruning") == 2
    assert text.count("snapshot") == 2
    assert "request-1" in text
    assert "request-2" in text
    assert text.index("request-1") < text.index("request-2")


def test_reverse_debug_original_log_path(tmp_path: Path) -> None:
    assert reverse_debug_original_log_path("anthropic", debug_log_dir=tmp_path) == (
        tmp_path / "anthropic-original.log"
    )
    assert reverse_debug_log_path("anthropic", debug_log_dir=tmp_path) == (
        tmp_path / "anthropic.log"
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
    payload = {"debug_request_seq": 1, "body": body, "path": "/openai/v1/messages"}
    append_original_debug_snapshot(original_path, payload)
    append_debug_snapshot(mutated_path, payload)

    original_text = original_path.read_text(encoding="utf-8")
    mutated_text = mutated_path.read_text(encoding="utf-8")
    assert "original-request" in original_text
    assert "snapshot" in mutated_text
    assert "original-request" not in mutated_text
    assert '"body": {' in original_text
    assert '"body": "{' not in original_text
    logged = json.loads(original_text.rsplit("---", 1)[-1].strip())
    assert logged["body"] == body
