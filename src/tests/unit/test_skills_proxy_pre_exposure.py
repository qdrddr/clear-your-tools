"""Proxy skills injection with pre-exposure and merge behavior."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cyt.agents.claude.skills_proxy import inject_skills_matches_into_anthropic_body
from cyt.skills.search import MatchedSkill


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _match(name: str, path: Path, body: str) -> MatchedSkill:
    return MatchedSkill(
        doc_id=name,
        file_path=str(path),
        markdown=body,
        name=name,
        score=1.0,
        token_count=50,
    )


def test_system_inject_replaces_stale_block_on_next_turn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        old_path = skills_dir / "old-skill.md"
        new_path = skills_dir / "new-skill.md"
        _write_skill(
            old_path,
            "---\nname: old-skill\ndescription: Old skill.\n---\n# Old\n\nOld skill body.\n",
        )
        _write_skill(
            new_path,
            "---\nname: new-skill\ndescription: New skill.\n---\n# New\n\nNew skill body.\n",
        )
        old_body = "# Old\n\nOld skill body.\n"
        body = {
            "model": "claude-test",
            "system": [
                {
                    "type": "text",
                    "text": (
                        "# MCP Server Instructions\n\n"
                        "Based on the user query added chunks of descriptions of skills (not entire skill). "
                        "The entire skill could be retrieved with the file path, though in most cases it likely "
                        "excessive.\n\n"
                        "<agent-skills>\n"
                        f'<skill name="old-skill" path="{old_path}">\n{old_body}</skill>\n'
                        "</agent-skills>"
                    ),
                },
            ],
            "messages": [{"role": "user", "content": "need the new skill"}],
        }
        config = {
            "network": {"proxy": {"reverse": {"inject_into_user_message": False}}},
            "pruning": {"inject_via": {"claude": "proxy"}},
        }
        matches = [_match("new-skill", new_path, "# New\n\nNew skill body.\n")]
        out, meta = inject_skills_matches_into_anthropic_body(
            body,
            matches,
            config=config,
        )
        system_text = "\n".join(
            str(block.get("text") or "") for block in out["system"] if isinstance(block, dict)
        )
        assert meta.skills_in > 0
        assert system_text.count("<agent-skills>") == 1
        assert "# MCP Server Instructions" in system_text
        assert 'name="old-skill"' not in system_text
        assert 'name="new-skill"' in system_text


def test_user_turn_inject_strips_prior_turn_skills_and_replaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        old_path = skills_dir / "old-skill.md"
        new_path = skills_dir / "new-skill.md"
        _write_skill(
            old_path,
            "---\nname: old-skill\ndescription: Old skill.\n---\n# Old\n\nOld skill body.\n",
        )
        _write_skill(
            new_path,
            "---\nname: new-skill\ndescription: New skill.\n---\n# New\n\nNew skill body.\n",
        )
        old_body = "# Old\n\nOld skill body.\n"
        body = {
            "model": "claude-test",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "first question\n\n"
                        "<agent-skills>\n"
                        f'<skill name="old-skill" path="{old_path}">\n{old_body}</skill>\n'
                        "</agent-skills>"
                    ),
                },
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "second question"},
            ],
        }
        config = {
            "network": {"proxy": {"reverse": {"inject_into_user_message": True}}},
            "pruning": {"inject_via": {"claude": "proxy"}},
        }
        out, meta = inject_skills_matches_into_anthropic_body(
            body,
            [_match("new-skill", new_path, "# New\n\nNew skill body.\n")],
            config=config,
        )
        assert meta.skills_in > 0
        first_turn = out["messages"][0]["content"]
        last_turn = out["messages"][2]["content"]
        assert isinstance(first_turn, str)
        assert isinstance(last_turn, str)
        assert "<agent-skills>" not in first_turn
        assert last_turn.count("<agent-skills>") == 1
        assert 'name="new-skill"' in last_turn
        assert 'name="old-skill"' not in last_turn


def test_next_turn_reinjects_skill_after_stale_block_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        old_path = skills_dir / "old-skill.md"
        _write_skill(
            old_path,
            "---\nname: old-skill\ndescription: Old skill.\n---\n# Old\n\nOld skill body.\n",
        )
        old_body = "# Old\n\nOld skill body.\n"
        body = {
            "model": "claude-test",
            "system": [
                {
                    "type": "text",
                    "text": (
                        "<agent-skills>\n"
                        f'<skill name="old-skill" path="{old_path}">\n{old_body}</skill>\n'
                        "</agent-skills>"
                    ),
                },
            ],
            "messages": [{"role": "user", "content": "same skill again"}],
        }
        config = {
            "network": {"proxy": {"reverse": {"inject_into_user_message": False}}},
            "pruning": {"inject_via": {"claude": "proxy"}},
        }
        out, meta = inject_skills_matches_into_anthropic_body(
            body,
            [_match("old-skill", old_path, old_body)],
            config=config,
        )
        assert meta.skills_in > 0
        assert out["system"][0]["text"].count("<agent-skills>") == 1
        assert 'name="old-skill"' in out["system"][0]["text"]
