"""Tests for workspace-aware hook config and HTTP catalog merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.hook.workspace_config import hook_catalog_url_for_payload, resolve_hook_request_config
from cyt_mcp.catalog import merge_catalog_payloads


def test_merge_catalog_payloads_workspace_overrides_global() -> None:
    base = {
        "agent": "cursor",
        "tools": [{"name": "a_tool", "input_schema": {}}],
        "degraded_servers": ["global-down"],
    }
    overlay = {
        "agent": "cursor",
        "tools": [{"name": "a_tool", "input_schema": {"type": "object", "properties": {}}}],
        "degraded_servers": ["ws-down"],
    }
    merged = merge_catalog_payloads(base, overlay)
    assert len(merged["tools"]) == 1
    assert merged["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert set(merged["degraded_servers"]) == {"global-down", "ws-down"}


def test_resolve_hook_request_config_merges_workspace_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    global_cfg = home / "config.yaml"
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text("pruning:\n  tools:\n    enabled: true\n", encoding="utf-8")

    ws_cfg = tmp_path / ".cursor" / "cyt" / "config" / "config.yaml"
    ws_cfg.parent.mkdir(parents=True)
    ws_cfg.write_text(
        "pruning:\n  tools:\n    hook:\n      tools_from: [cyt_mcp]\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "cyt.hook.workspace_config.DEFAULT_USER_CONFIG_PATH",
        global_cfg,
    )
    monkeypatch.setattr(
        "cyt.hook.workspace_config.load_config",
        lambda path=None: __import__("yaml").safe_load(global_cfg.read_text(encoding="utf-8")),
    )

    payload = {"workspace_roots": [str(tmp_path.resolve())]}
    merged, workspace = resolve_hook_request_config(payload, "cursor")
    assert workspace == tmp_path.resolve()
    assert merged["pruning"]["tools"]["hook"]["tools_from"] == ["cyt_mcp"]


def test_hook_catalog_url_includes_workspace_query() -> None:
    from urllib.parse import parse_qs, urlparse

    config = {
        "pruning": {
            "tools": {
                "hook": {
                    "cyt_mcp": {
                        "catalog_url": "http://127.0.0.1:8765/catalog",
                    },
                },
            },
        },
    }
    payload = {"workspace_roots": ["/repo/path"]}
    url = hook_catalog_url_for_payload(config, payload)
    assert "workspace=" in url
    workspace = parse_qs(urlparse(url).query)["workspace"][0]
    assert workspace.replace("\\", "/") == "/repo/path"
