"""Tests for skill read interceptor permission denies."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cyt.skills.agent_interceptor import run_skill_read_intercept


def test_run_skill_read_intercept_denies_disabled_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_dir = tmp_path / "blocked-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: blocked-skill\ndescription: x\n---\n# Body\n", encoding="utf-8")

    global_config_path = tmp_path / "global-config.yaml"
    global_config_path.write_text(
        yaml.dump(
            {
                "skills": {"enabled": True, "directories": [str(tmp_path)]},
                "agents": {
                    "cursor": {"skills": {"permissions": {"deny": ["blocked-skill"], "allow": []}}},
                },
            },
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt.permissions.merge.DEFAULT_USER_CONFIG_PATH",
        global_config_path,
    )

    config = {
        "skills": {"enabled": True, "directories": [str(tmp_path)]},
    }
    payload = {
        "cyt_intercept_read_path": str(skill_md),
        "cyt_intercept_query": "how to use",
        "cwd": str(tmp_path),
        "cyt_agent": "cursor",
    }

    with patch("cyt.skills.agent_interceptor.skills_enabled", return_value=True):
        result = run_skill_read_intercept(payload, config)

    assert result["permission"] == "deny"
