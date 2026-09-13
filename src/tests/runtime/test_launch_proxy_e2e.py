"""End-to-end tests for cyt launch proxy subprocess + upstream forwarding."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cyt.launch.proxy_guard import LOCAL_HOST
from tests.support.runtime_e2e_fixtures import (
    assert_proxy_health,
    ephemeral_port,
    isolated_tools_hook_workspace,
    launch_proxy,
    mock_upstream_server,
    proxy_launch_config,
    write_yaml_config,
)

pytestmark = pytest.mark.runtime


@pytest.mark.runtime
def test_launch_proxy_forwards_pruned_tools_to_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = isolated_tools_hook_workspace(tmp_path)

    with mock_upstream_server() as upstream:
        config_path = tmp_path / "proxy-config.yaml"
        write_yaml_config(
            config_path,
            proxy_launch_config(
                upstream_url=upstream.base_url,
                definitions_path=workspace.definitions_path or Path(""),
                bm25_index_dir=workspace.bm25_index_dir or tmp_path / "bm25",
                stats_db=tmp_path / "proxy-stats.db",
            ),
        )
        proxy_port = ephemeral_port()
        with launch_proxy(config_path, port=proxy_port, launch_agent="claude"):
            assert_proxy_health(proxy_port)
            request_body = {
                "model": "claude-runtime-e2e",
                "max_tokens": 64,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "read a file from disk"}],
                    },
                ],
                "tools": json.loads(
                    (workspace.definitions_path or Path()).read_text(encoding="utf-8"),
                )["tools"],
            }
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"http://{LOCAL_HOST}:{proxy_port}/anthropic/v1/messages",
                    json=request_body,
                    headers={
                        "content-type": "application/json",
                        "x-api-key": "runtime-e2e-test-key",
                        "anthropic-version": "2023-06-01",
                    },
                )

    assert response.status_code == 200
    assert upstream.captured.get("json_body") is not None
    forwarded_tools = upstream.captured["json_body"].get("tools") or []
    forwarded_names = {str(tool.get("name")) for tool in forwarded_tools if isinstance(tool, dict)}
    assert forwarded_names
    assert len(forwarded_names) <= len(request_body["tools"])
    assert "mcp__filesystem__read_file" in forwarded_names
