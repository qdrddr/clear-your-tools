"""Tests for default skills permission path rules."""

from __future__ import annotations

from cyt.config import _default_user_config_dict, bundled_user_config_sections


def test_default_user_config_includes_codex_system_path_denies() -> None:
    config = _default_user_config_dict()
    deny = config["skills"]["permissions"]["deny"]
    assert "path:.codex/skills/.system" in deny
    assert "path:~/.codex/skills/.system" in deny


def test_bundled_user_config_sections_includes_permissions() -> None:
    sections = bundled_user_config_sections()
    assert "skills" in sections
    assert "path:.codex/skills/.system" in sections["skills"]["permissions"]["deny"]
