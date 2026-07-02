"""Tests for definitions JSON tool catalog loading."""

from __future__ import annotations

import json
from pathlib import Path

from cyt.tools.sources.definitions import load_definitions_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_definitions_sample.json"


def test_load_definitions_file_parses_tools_array() -> None:
    tools = load_definitions_file(FIXTURE)
    assert len(tools) == 3
    assert tools[0]["name"] == "mcp__filesystem__read_file"
    assert "input_schema" in tools[0]


def test_load_definitions_bare_array(tmp_path: Path) -> None:
    payload = [{"name": "mcp__srv__tool", "description": "demo"}]
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    tools = load_definitions_file(path)
    assert tools == [{"name": "mcp__srv__tool", "description": "demo"}]
