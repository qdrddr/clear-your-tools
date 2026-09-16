"""Unit tests for ``cyt db maintain`` CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cyt.db.cli import run_db_maintain


def test_db_maintain_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    examples_db = tmp_path / "tool_examples.db"
    tier_db = tmp_path / "tier_state.db"
    config = {
        "tools": {
            "examples": {
                "enabled": True,
                "database": {"path": str(examples_db)},
            },
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(tier_db)},
                "retention": {"enabled": True},
            },
        },
    }
    monkeypatch.setattr("cyt.db.cli.load_config", lambda: config)

    args = argparse.Namespace(dry_run=True, no_vacuum=False, json=True)
    assert run_db_maintain(args) == 0
