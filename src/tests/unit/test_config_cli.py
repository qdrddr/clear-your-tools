#!/usr/bin/env python3
"""Tests for cyt config CLI."""

from __future__ import annotations

import pytest

from cyt.migrations.cli import main


def test_config_history_prints(capsys: pytest.CaptureFixture[str]) -> None:
    main(["history"])
    out = capsys.readouterr().out
    assert "001_add_schema_version" in out
    assert "005_skills_agent_directories" in out
