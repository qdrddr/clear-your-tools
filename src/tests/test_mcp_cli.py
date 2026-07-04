"""Tests for ``cyt mcp save``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tools.mcp_cli import run_mcp_save
from cyt.tools.sources.mcp_client import McpServerFetchResult


def _save_args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _write_mcp_client(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": "echo",
                        "args": ["demo"],
                    },
                },
            },
        ),
        encoding="utf-8",
    )


def test_mcp_save_writes_definitions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client_path = tmp_path / "mcp.json"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "pruning:",
                "  tools:",
                "    hook:",
                f"      mcp_client_file: {client_path}",
                "      mcp_definitions_file: mcp-definitions.json",
            ],
        ),
        encoding="utf-8",
    )
    _write_mcp_client(client_path)
    output_path = tmp_path / "out.json"
    sample_tools = [{"name": "mcp__demo__search", "description": "Search"}]
    results = [
        McpServerFetchResult(
            server_name="demo",
            status="ok",
            tools=(sample_tools[0],),
        ),
    ]

    with patch(
        "cyt.tools.mcp_cli.fetch_mcp_client_tools",
        return_value=(sample_tools, results),
    ):
        run_mcp_save(_save_args(config=config_path, file=output_path))

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["tools"] == sample_tools
    captured = capsys.readouterr()
    assert "Wrote 1 tools" in captured.out
    assert "demo: 1 tools" in captured.err


def test_mcp_save_exits_when_client_file_missing_non_tty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning: {}\n", encoding="utf-8")
    missing_client = tmp_path / "missing-mcp.json"

    with (
        patch("cyt.tools.mcp_cli.tools_hook_mcp_client_file", return_value=missing_client),
        patch("cyt.tools.mcp_cli.sys.stdin.isatty", return_value=False),
        pytest.raises(SystemExit, match="MCP client config not found"),
    ):
        run_mcp_save(_save_args(config=config_path, file=tmp_path / "out.json"))


def test_mcp_save_prompts_and_persists_client_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning: {}\n", encoding="utf-8")
    client_path = tmp_path / "mcp.json"
    _write_mcp_client(client_path)
    output_path = tmp_path / "definitions.json"
    default_client = tmp_path / "default-mcp.json"

    monkeypatch.setattr("cyt.tools.mcp_cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.tools.mcp_cli._prompt", lambda _text, _default: str(client_path))
    with (
        patch("cyt.tools.mcp_cli.tools_hook_mcp_client_file", return_value=default_client),
        patch(
            "cyt.tools.mcp_cli.fetch_mcp_client_tools",
            return_value=([], []),
        ),
    ):
        run_mcp_save(_save_args(config=config_path, file=output_path))

    saved = config_path.read_text(encoding="utf-8")
    assert "mcp_client_file" in saved
    assert str(client_path) in saved
    captured = capsys.readouterr()
    assert "Saved MCP client file" in captured.out


def test_mcp_save_rejects_mcp_json_as_config(tmp_path: Path) -> None:
    mcp_path = tmp_path / "mcp.json"
    _write_mcp_client(mcp_path)

    with pytest.raises(SystemExit, match="looks like an MCP client JSON file"):
        run_mcp_save(_save_args(config=mcp_path, file=tmp_path / "out.json"))


def test_mcp_save_overlay_writes_single_hook_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "pruning:",
                "  tools:",
                "    sequence:",
                "    - llm",
                "    hook:",
                "      tools_from: definitions",
            ],
        ),
        encoding="utf-8",
    )
    client_path = tmp_path / "mcp.json"
    _write_mcp_client(client_path)

    with patch(
        "cyt.tools.mcp_cli.fetch_mcp_client_tools",
        return_value=([], []),
    ):
        run_mcp_save(_save_args(config=config_path, file=tmp_path / "out.json"))

    saved = config_path.read_text(encoding="utf-8")
    assert saved.count("mcp_client_file:") == 1
    assert saved.count("mcp_definitions_file:") == 1
    assert "\n    tools:\n      hook:" not in saved
