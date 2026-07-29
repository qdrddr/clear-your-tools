"""Stable paths for tests regardless of category subfolder depth."""

from __future__ import annotations

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = TESTS_ROOT / "fixtures"
REPO_ROOT = TESTS_ROOT.parents[1]
