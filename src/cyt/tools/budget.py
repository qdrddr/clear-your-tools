"""Tools hook injection budget and allow checks."""

from __future__ import annotations

from typing import Any, Literal

from cyt.config import tools_inject_via
from cyt.skills.budget import (
    count_hook_request_tokens,
    resolve_inject_budget,
    skills_budget_precheck,
)

ToolsInjectPath = Literal["hook", "proxy"]


def tools_inject_allowed(
    config: dict[str, Any],
    inject_path: ToolsInjectPath,
    *,
    cli_prompt: bool = False,
) -> bool:
    del cli_prompt
    return tools_inject_via(config) == inject_path


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
