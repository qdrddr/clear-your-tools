"""Claude agent capabilities."""

from __future__ import annotations

from cyt.agents._protocol import (
    AgentCapabilities,
    HookCapability,
    LaunchCapability,
    ProxyCapability,
    SkillsHookCapability,
    SkillsProxyCapability,
)
from cyt.agents.claude import hook as hook_mod
from cyt.agents.claude import launch as launch_mod
from cyt.agents.claude import skills_hook as skills_hook_mod
from cyt.agents.claude import skills_proxy as skills_proxy_mod


def capabilities() -> AgentCapabilities:
    return AgentCapabilities(
        name="claude",
        launch=LaunchCapability(run=launch_mod.run),
        hook=HookCapability(
            settings_path=hook_mod.SETTINGS_PATH,
            skills_dir=hook_mod.SKILLS_DIR,
        ),
        proxy=ProxyCapability(configure=None, restore=None),
        skills_hook=SkillsHookCapability(
            skills_dir=skills_hook_mod.CLAUDE_SKILLS_DIR,
            normalize_payload=skills_hook_mod.normalize_payload,
            transcript_agent="claude",
            parse_last_assistant=skills_hook_mod.last_assistant_from_records,
            parse_model_from_records=skills_hook_mod.model_from_records,
        ),
        skills_proxy=SkillsProxyCapability(
            upstream_kind=skills_proxy_mod.UPSTREAM_KIND,
            inject_matches_into_body=skills_proxy_mod.inject_skills_matches_into_anthropic_body,
            finish_deferred=skills_proxy_mod.finish_deferred_skills_anthropic,
            skills_search_query=skills_proxy_mod.proxy_skills_search_query,
        ),
    )
