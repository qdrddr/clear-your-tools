"""Smoke tests for clear-your-tools installed from PyPI."""

from __future__ import annotations

import os
import subprocess

import cyt
from cyt.indexer import count_tokens


def test_version_matches_release() -> None:
    expected = os.environ["CYT_RELEASE_VERSION"]
    assert cyt.__version__ == expected


def test_indexer_count_tokens_via_transitive_sdk() -> None:
    assert count_tokens("hello") > 0


def test_cyt_console_script_help() -> None:
    result = subprocess.run(
        ["cyt", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
