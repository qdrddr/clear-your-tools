"""Cursor agent capabilities."""

from __future__ import annotations

from cyt.agents._protocol import (
    AgentCapabilities,
    HookCapability,
    LaunchCapability,
    SkillsHookCapability,
)
from cyt.agents.cursor import hook as hook_mod
from cyt.agents.cursor import launch as launch_mod
from cyt.agents.cursor import skills_hook as skills_hook_mod


def capabilities() -> AgentCapabilities:
    return AgentCapabilities(
        name="cursor",
        launch=LaunchCapability(run=launch_mod.run),
        hook=HookCapability(
            settings_path=hook_mod.CURSOR_HOOKS_PATH,
            skills_dir=hook_mod.CURSOR_SKILLS_DIR,
            install_hooks=launch_mod.ensure_cursor_hooks_for_launch,
        ),
        proxy=None,
        skills_hook=SkillsHookCapability(
            skills_dir=skills_hook_mod.CURSOR_SKILLS_DIR,
            normalize_payload=skills_hook_mod.normalize_payload,
            transcript_agent="cursor",
            parse_last_assistant=skills_hook_mod.last_assistant_from_records,
            parse_model_from_records=None,
        ),
        skills_proxy=None,
    )
