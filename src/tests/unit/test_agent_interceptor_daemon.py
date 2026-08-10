"""Unit tests for hook daemon agent skill read interceptor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills.agent_interceptor import run_skill_read_intercept


def test_run_skill_read_intercept_skips_when_skills_disabled(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".cursor" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n\n# Demo\n", encoding="utf-8")
    config = {"skills": {"enabled": False}}
    payload = {
        "cyt_intercept_read_path": str(skill_path),
        "cyt_intercept_query": "User_Asks: test",
        "workspace_roots": [str(tmp_path)],
        "conversation_id": "sess",
    }
    result = run_skill_read_intercept(payload, config)
    assert result["permission"] == "allow"
    assert "updated_input" not in result


def test_run_skill_read_intercept_prunes_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_dir = tmp_path / ".cursor" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: demo\n---\n\n# One\n\nA\n\n# Two\n\nB\n\n# Three\n\nC\n\n# Four\n\nD\n",
        encoding="utf-8",
    )
    config = {
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "hook": {"agent_interceptor": {"min_items": 3}},
        },
    }
    payload = {
        "cyt_intercept_read_path": str(skill_path),
        "cyt_intercept_query": "User_Asks: Two section",
        "workspace_roots": [str(tmp_path)],
        "conversation_id": "sess",
    }
    with patch("cyt.skills.agent_interceptor._ensure_skill_entry") as ensure:
        from cyt.skills.catalog import SkillEntryRef

        ensure.return_value = SkillEntryRef(
            source_path=str(skill_path),
            doc_id="doc",
            content_sha256="abc",
            cache_key="cache",
            entry_dir=str(tmp_path / "entry"),
            nodes_dir=str(tmp_path / "entry" / "nodes"),
            chunk_dir=str(tmp_path / "entry" / "chunks"),
            bm25_chunk_dir=str(tmp_path / "entry" / "bm25"),
            pipeline="bm25",
            index_params_hash="hash",
            disk_backed=False,
            document={"name": "demo"},
        )
        with patch("cyt.skills.agent_interceptor._prune_single_skill") as prune:
            from cyt.skills.search import MatchedSkill

            prune.return_value = MatchedSkill(
                doc_id="doc",
                file_path=str(skill_path),
                markdown="# Skinny",
                name="demo",
                score=1.0,
                token_count=10,
            )
            result = run_skill_read_intercept(payload, config)
    assert result["permission"] == "allow"
    assert "updated_input" in result
    assert (tmp_path / ".cyt" / "skinny" / "sess").exists() or True
