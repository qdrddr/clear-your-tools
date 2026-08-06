"""Hook-gated query augmentation for tool pruning stages."""

from __future__ import annotations

from typing import Any

from cyt.config import load_config
from cyt.tools.budget import tools_inject_allowed

__all__ = [
    "TOOLS_HOOK_OPTIONAL_SCOPE_INSTRUCTION",
    "tools_pruning_query",
]

TOOLS_HOOK_OPTIONAL_SCOPE_INSTRUCTION = (
    "Include optional path, cwd, working_root, or project parameters only when required "
    "to scope a tool that survives relevance to the user's prompt. Otherwise, omit them."
)


def tools_pruning_query(
    query: str,
    config: dict[str, Any] | None,
    *,
    for_hook: bool = False,
) -> str:
    """Return pruning query, appending hook optional-scope guidance when applicable."""
    if not query.strip():
        return query
    if not for_hook:
        return query
    cfg = config if config is not None else load_config()
    if not tools_inject_allowed(cfg, "hook"):
        return query
    return f"{query.rstrip()}\n\n{TOOLS_HOOK_OPTIONAL_SCOPE_INSTRUCTION}"
