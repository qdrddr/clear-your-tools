"""Tests for ``cyt executor save``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tools.executor_cli import run_executor_save

_SAMPLE_TOOLS = [
    {
        "name": "tools.demo.org.default.search",
        "description": "Search",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _save_args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_executor_save_writes_definitions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "pruning:",
                "  tools:",
                "    hook:",
                "      executor_url: http://localhost:4789",
                "      mcp_definitions_file: mcp-definitions.json",
            ],
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "out.json"

    with patch(
        "cyt.tools.executor_cli.fetch_executor_tools_for_cli",
        return_value=_SAMPLE_TOOLS,
    ):
        run_executor_save(_save_args(config=config_path, file=output_path))

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["tools"] == _SAMPLE_TOOLS
    captured = capsys.readouterr()
    assert "Wrote 1 tools" in captured.out


def test_executor_save_exits_when_executor_url_missing_non_tty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "pruning:",
                "  tools:",
                "    hook:",
                "      executor_url: ''",
            ],
        ),
        encoding="utf-8",
    )

    with (
        patch("cyt.tools.executor_cli.sys.stdin.isatty", return_value=False),
        pytest.raises(SystemExit, match="Executor URL not configured"),
    ):
        run_executor_save(_save_args(config=config_path, file=tmp_path / "out.json"))


def test_executor_save_prompts_and_persists_executor_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning: {}\n", encoding="utf-8")
    output_path = tmp_path / "definitions.json"

    monkeypatch.setattr("cyt.tools.executor_cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cyt.tools.executor_cli._prompt",
        lambda _text, _default: "http://localhost:4789",
    )
    with (
        patch(
            "cyt.tools.executor_cli.tools_hook_executor_url",
            side_effect=["", "http://localhost:4789"],
        ),
        patch(
            "cyt.tools.executor_cli.fetch_executor_tools_for_cli",
            return_value=_SAMPLE_TOOLS,
        ),
    ):
        run_executor_save(_save_args(config=config_path, file=output_path))

    saved = config_path.read_text(encoding="utf-8")
    assert "executor_url:" in saved
    assert "http://localhost:4789" in saved
    captured = capsys.readouterr()
    assert "Saved executor URL" in captured.out
