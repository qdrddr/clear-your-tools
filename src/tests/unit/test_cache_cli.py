"""Tests for ``cyt cache clear``."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cyt.cache.cli import run_cache_clear


def test_cache_clear_requires_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    code = run_cache_clear(
        argparse.Namespace(tools=False, skills=False, bm25=False, catalog=False, all=False),
    )
    assert code == 2


def test_cache_clear_tools_memory_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    code = run_cache_clear(
        argparse.Namespace(tools=True, skills=False, bm25=False, catalog=False, all=False),
    )
    assert code == 0
