"""Smoke tests for cyt-indexer-sdk installed from PyPI."""

from __future__ import annotations

import pytest

from cyt_indexer import build_catalog_index

from test_example_snapshot import (
    catalog_dict_from_snapshot,
    extract_snapshot_parts,
    load_snapshot,
    resolve_snapshot_path,
    write_output,
)


def test_build_catalog_index_from_registry() -> None:
    tool = {
        "id": "mcp__test__foo",
        "server": "test",
        "tool": "mcp__test__foo",
        "summary": "A test tool",
        "full_schema": {
            "id": "mcp__test__foo",
            "name": "mcp__test__foo",
            "description": "A test tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "required_field": {"type": "string"},
                    "optional_field": {"type": "string", "description": "opt"},
                },
                "required": ["required_field"],
            },
        },
    }
    index = build_catalog_index([tool], [])
    assert "schemas/decomposed/mcp__test__foo.json" in index.files


def test_retrieve_from_example_file(
    example_file: str | None,
    output_file: str | None,
) -> None:
    if example_file is None:
        pytest.skip("pass --file to run against a local debug snapshot")
    snapshot_path = resolve_snapshot_path(example_file)
    data = load_snapshot(snapshot_path)
    _build_tools, _survivor, _expected = extract_snapshot_parts(data)

    catalog = catalog_dict_from_snapshot(data)
    json_chunks = catalog.get("json") or []
    md_chunks = catalog.get("md") or []
    assert json_chunks, "build_catalog_index produced no json chunks"
    assert md_chunks, "build_catalog_index produced no md enum chunks"
    assert any(
        "/schemas/decomposed/" in entry.get("file_path", "")
        and entry.get("file_path", "").endswith(".json")
        for entry in json_chunks
        if isinstance(entry, dict)
    ), "expected per-property decomposed json chunks"

    write_output(catalog, output_file)
