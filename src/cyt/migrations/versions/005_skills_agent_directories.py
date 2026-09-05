"""Move agent-specific skill directories from global skills.directories to agents.*.skills.directories."""

from __future__ import annotations

from typing import Any

from cyt.config import split_skill_directories_by_scope
from cyt.migrations.base import ConfigScope, deep_copy_config, set_schema_stamp

revision = "005_skills_agent_directories"
down_revision = "004_permissions_agents_layout"
applies_to = "both"

_DEFAULT_GLOBAL_DIRECTORY = "~/.agents/skills"


def _ensure_agents_block(cfg: dict[str, Any]) -> dict[str, Any]:
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        cfg["agents"] = agents
    return agents


def _ensure_agent_skills_block(agents: dict[str, Any], agent: str) -> dict[str, Any]:
    agent_block = agents.get(agent)
    if not isinstance(agent_block, dict):
        agent_block = {}
        agents[agent] = agent_block
    skills = agent_block.get("skills")
    if not isinstance(skills, dict):
        skills = {}
        agent_block["skills"] = skills
    return skills


def _append_unique(paths: list[str], raw: str) -> None:
    text = str(raw).strip()
    if not text:
        return
    if text in paths:
        return
    paths.append(text)


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    skills = result.get("skills")
    if not isinstance(skills, dict):
        set_schema_stamp(result, revision)
        return result

    raw_directories = skills.get("directories")
    if not isinstance(raw_directories, list) or not raw_directories:
        set_schema_stamp(result, revision)
        return result

    global_dirs, by_agent = split_skill_directories_by_scope(
        [str(item) for item in raw_directories],
    )
    if not global_dirs:
        global_dirs = [_DEFAULT_GLOBAL_DIRECTORY]

    skills["directories"] = global_dirs

    agents = _ensure_agents_block(result)
    for agent, migrated_dirs in by_agent.items():
        if not migrated_dirs:
            continue
        agent_skills = _ensure_agent_skills_block(agents, agent)
        existing = agent_skills.get("directories")
        merged: list[str] = []
        if isinstance(existing, list):
            for item in existing:
                _append_unique(merged, str(item))
        for item in migrated_dirs:
            _append_unique(merged, item)
        agent_skills["directories"] = merged

    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 005_skills_agent_directories")
