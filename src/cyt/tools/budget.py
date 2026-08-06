"""Tools hook injection budget and allow checks."""

from __future__ import annotations

from typing import Any, Literal

from cyt.config import tools_enabled, tools_inject_via
from cyt.skills.budget import (
    count_hook_request_tokens,
    resolve_inject_budget,
    skills_budget_precheck,
)

ToolsInjectPath = Literal["hook", "proxy"]


def resolve_tools_inject_agent(
    config: dict[str, Any],
    inject_path: ToolsInjectPath,
    *,
    agent: str | None = None,
    upstream_kind: str | None = None,
) -> str:
    if agent is not None:
        return agent
    if inject_path == "hook":
        from cyt.config import tools_hook_cyt_mcp_agent

        return tools_hook_cyt_mcp_agent(config)
    from cyt.launch.upstream import launch_agent_for_upstream_kind

    resolved = launch_agent_for_upstream_kind(upstream_kind)
    if resolved is not None:
        return resolved
    return "claude"


def tools_inject_allowed(
    config: dict[str, Any],
    inject_path: ToolsInjectPath,
    *,
    agent: str | None = None,
    upstream_kind: str | None = None,
    cli_prompt: bool = False,
) -> bool:
    del cli_prompt
    if not tools_enabled(config):
        return False
    resolved_agent = resolve_tools_inject_agent(
        config,
        inject_path,
        agent=agent,
        upstream_kind=upstream_kind,
    )
    return tools_inject_via(config, agent=resolved_agent) == inject_path


def tools_budget_precheck(config: dict[str, Any] | None = None) -> bool:
    return skills_budget_precheck(config)


def resolve_tools_inject_budget(
    config: dict[str, Any],
    *,
    total_request_tokens: int,
) -> tuple[int, dict[str, int]]:
    budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=total_request_tokens,
    )
    return budget.effective_max, budget.debug


def count_tools_hook_request_tokens(payload: dict[str, Any]) -> int:
    return count_hook_request_tokens(payload)
