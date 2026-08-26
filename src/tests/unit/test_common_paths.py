"""Tests for cyt.common.paths helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cyt.common.paths import shorten_home_path


def test_shorten_home_path_uses_tilde_for_home_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    project = home / "git" / "clear-your-tools"
    project.mkdir(parents=True)
    assert shorten_home_path(str(project)) == "~/git/clear-your-tools"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter path shape")
def test_shorten_home_path_windows_drive_letter() -> None:
    home = Path.home()
    candidate = home / "git" / "clear-your-tools"
    if not candidate.parent.exists():
        pytest.skip("home/git not present")
    assert shorten_home_path(str(candidate)).startswith("~/")
