"""Definitions catalog fingerprint uses content hash, not mtime."""

from __future__ import annotations

from pathlib import Path

from cyt.tools.definitions_catalog import _definitions_fingerprint


def test_definitions_fingerprint_is_content_addressed(tmp_path: Path) -> None:
    path = tmp_path / "tools.json"
    path.write_text('[{"name": "demo", "input_schema": {}}]', encoding="utf-8")
    first = _definitions_fingerprint(path)
    path.touch()
    second = _definitions_fingerprint(path)
    assert first == second
