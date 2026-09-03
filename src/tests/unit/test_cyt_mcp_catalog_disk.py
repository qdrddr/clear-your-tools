"""Unit tests for cyt-mcp catalog disk cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash, write_disk_catalog


def test_raw_catalog_content_hash_stable() -> None:
    tools = [
        {"name": "filesystem_read_file", "input_schema": {"type": "object", "properties": {}}},
        {"name": "context7_query", "input_schema": {"type": "object", "properties": {"q": {}}}},
    ]
    assert raw_catalog_content_hash(tools) == raw_catalog_content_hash(list(reversed(tools)))


def test_write_disk_catalog_skips_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    tools = [{"name": "filesystem_read_file", "input_schema": {"type": "object"}}]
    content_hash = raw_catalog_content_hash(tools)
    assert write_disk_catalog("cursor", agent="cursor", tools=tools, content_hash=content_hash) == (
        "disk_write_created"
    )
    assert write_disk_catalog("cursor", agent="cursor", tools=tools, content_hash=content_hash) == (
        "disk_write_skipped"
    )
    assert (tmp_path / "by-hash" / f"{content_hash}.json").is_file()
    assert (tmp_path / "backends" / "cursor.json").is_file()


def test_scope_config_fingerprint_and_merged_slug(tmp_path: Path) -> None:
    from cyt.cyt_mcp.catalog_disk import merged_hook_catalog_slug, scope_config_fingerprint

    global_agg = tmp_path / "global-aggregator.yaml"
    global_defs = tmp_path / "global.json"
    ws_agg = tmp_path / "ws-aggregator.yaml"
    ws_defs = tmp_path / "ws.json"
    global_agg.write_text("global: true\n", encoding="utf-8")
    global_defs.write_text('{"mcpServers": {"a": {}}}', encoding="utf-8")
    ws_agg.write_text("workspace: true\n", encoding="utf-8")
    ws_defs.write_text('{"mcpServers": {"b": {}}}', encoding="utf-8")

    global_fp = scope_config_fingerprint(global_agg, global_defs)
    ws_fp = scope_config_fingerprint(ws_agg, ws_defs)
    merged = merged_hook_catalog_slug(global_fp, ws_fp)

    assert global_fp != ws_fp
    assert merged != global_fp
    assert merged == merged_hook_catalog_slug(global_fp, ws_fp)
    assert merged_hook_catalog_slug(global_fp, None) == global_fp


def test_cache_key_uses_scope_fingerprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cyt.cyt_mcp.catalog import _cache_key_for_config

    global_agg = tmp_path / "mcp-aggregator.yaml"
    global_defs = tmp_path / "mcp" / "cursor.json"
    global_defs.parent.mkdir(parents=True)
    global_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    ws_root = tmp_path / "repo"
    cyt_dir = ws_root / ".cursor" / "cyt"
    cyt_dir.mkdir(parents=True)
    (cyt_dir / "mcp").mkdir()
    (cyt_dir / "config").mkdir()
    (cyt_dir / "config" / "mcp-aggregator.yaml").write_text(
        "default_agent: cursor\n",
        encoding="utf-8",
    )
    (cyt_dir / "mcp" / "cursor.json").write_text('{"mcpServers": {"ws": {}}}', encoding="utf-8")

    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog._global_scope_paths",
        lambda agent: (global_agg, global_defs),
    )

    config_global = {
        "pruning": {
            "tools": {
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {"agent": "cursor", "catalog_url": "http://127.0.0.1:8765/catalog"},
                },
            },
        },
    }
    key_global = _cache_key_for_config(config_global)

    config_merged = {
        "pruning": {
            "tools": {
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {
                        "agent": "cursor",
                        "catalog_url": f"http://127.0.0.1:8765/catalog?workspace={ws_root}",
                    },
                },
            },
        },
    }
    key_merged = _cache_key_for_config(config_merged)

    assert key_global.slug != key_merged.slug
    assert key_merged.workspace == str(ws_root)
