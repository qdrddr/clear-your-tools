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
