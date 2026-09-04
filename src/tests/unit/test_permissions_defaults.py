"""Tests for default permissions in defaults.yaml and config seeding."""

from __future__ import annotations

from pathlib import Path

from cyt.config import (
    _default_user_config_dict,
    bundled_user_config_sections,
    load_bundled_defaults_yaml,
    load_config,
)


def test_bundled_defaults_yaml_includes_codex_system_path_denies() -> None:
    bundled = load_bundled_defaults_yaml()
    deny = bundled["skills"]["permissions"]["deny"]
    assert "path:.codex/skills/.system" in deny
    assert "path:~/.codex/skills/.system" in deny


def test_default_user_config_includes_codex_system_path_denies() -> None:
    config = _default_user_config_dict()
    deny = config["skills"]["permissions"]["deny"]
    assert "path:.codex/skills/.system" in deny
    assert "path:~/.codex/skills/.system" in deny


def test_bundled_user_config_sections_includes_permissions() -> None:
    sections = bundled_user_config_sections()
    assert "skills" in sections
    assert "path:.codex/skills/.system" in sections["skills"]["permissions"]["deny"]


def test_load_config_does_not_inject_permissions_from_bundled_defaults(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("skills:\n  enabled: false\n", encoding="utf-8")
    loaded = load_config(config_path)
    assert "permissions" not in loaded.get("skills", {})
