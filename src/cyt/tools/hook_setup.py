"""Interactive wizard helpers for tools hook injection configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import (
    DEFAULT_TOOLS_HOOK_EXECUTOR_URL,
    DEFAULT_TOOLS_HOOK_MCP_DEFINITIONS_FILE,
    DEFAULT_TOOLS_HOOK_TOOLS_FROM,
    inject_via,
    load_config,
    resolved_tools_hook_file,
    save_user_config,
    tools_hook_file_missing,
    tools_hook_tools_from,
)
from cyt.proxy.setup_wizard import _prompt, _prompt_choice, _prompt_yes_no

ToolsSetupContext = Literal["hook", "setup", "launch"]

_TOOLS_FROM_CHOICES = ("executor", "definitions")


def build_tools_hook_config_overlay(
    *,
    tools_from: str,
    executor_url: str,
    mcp_definitions_file: str,
    executor_token_var: str | None = None,
) -> dict[str, Any]:
    """Return a ``pruning.tools`` overlay fragment (``hook`` settings only)."""
    hook: dict[str, Any] = {
        "tools_from": tools_from,
        "executor_url": executor_url,
        "mcp_definitions_file": mcp_definitions_file,
    }
    if executor_token_var:
        hook["executor_token_var"] = executor_token_var
    return {"hook": hook}


def build_pruning_tools_hook_save_overlay(
    *,
    tools_from: str,
    executor_url: str,
    mcp_definitions_file: str,
    executor_token_var: str | None = None,
) -> dict[str, Any]:
    """Return a full user-config overlay for ``pruning.tools.hook``."""
    return {
        "pruning": {
            "tools": build_tools_hook_config_overlay(
                tools_from=tools_from,
                executor_url=executor_url,
                mcp_definitions_file=mcp_definitions_file,
                executor_token_var=executor_token_var,
            ),
        },
    }


def prompt_tools_hook_config(
    existing: dict[str, Any],
    *,
    context: ToolsSetupContext,
    inject_mode: str | None = None,
) -> dict[str, Any]:
    """Prompt for tools hook settings; return pruning.tools overlay fragment."""
    pruning = existing.get("pruning")
    pruning_cfg = pruning if isinstance(pruning, dict) else {}
    tools_cfg = pruning_cfg.get("tools")
    tools = tools_cfg if isinstance(tools_cfg, dict) else {}
    hook_cfg = tools.get("hook")
    hook = hook_cfg if isinstance(hook_cfg, dict) else {}

    active_inject = inject_mode or inject_via(existing)
    if context == "hook":
        active_inject = "hook"

    print("\n--- Tool hook injection ---")

    current_from = str(hook.get("tools_from", DEFAULT_TOOLS_HOOK_TOOLS_FROM)).strip().lower()
    if context == "hook":
        from_default = "executor"
    elif current_from in {"client", "executor"}:
        from_default = "executor"
    else:
        from_default = current_from if current_from in _TOOLS_FROM_CHOICES else "executor"

    executor_default = str(hook.get("executor_url", DEFAULT_TOOLS_HOOK_EXECUTOR_URL))
    definitions_default = str(
        hook.get("mcp_definitions_file", DEFAULT_TOOLS_HOOK_MCP_DEFINITIONS_FILE),
    )

    if active_inject == "hook":
        tools_from = _prompt_choice(
            "Tool catalog source (executor | definitions)",
            list(_TOOLS_FROM_CHOICES),
            default_index=_TOOLS_FROM_CHOICES.index(from_default),
        )
        if tools_from == "definitions":
            path_text = _prompt("MCP definitions file", definitions_default)
            path_text = str(Path(path_text).expanduser())
            if not Path(path_text).is_file():
                print(
                    f"Note: {path_text} does not exist yet; hook will skip tool injection until it does.",
                    file=sys.stderr,
                )
            return build_tools_hook_config_overlay(
                tools_from=tools_from,
                executor_url=executor_default,
                mcp_definitions_file=path_text,
            )

        url_text = _prompt("Executor base URL", executor_default).strip().rstrip("/")
        if not url_text:
            print(
                "Note: executor URL is empty; hook will skip tool injection until configured.",
                file=sys.stderr,
            )
        return build_tools_hook_config_overlay(
            tools_from=tools_from,
            executor_url=url_text,
            mcp_definitions_file=definitions_default,
        )

    tools_from = from_default
    return build_tools_hook_config_overlay(
        tools_from=tools_from,
        executor_url=executor_default,
        mcp_definitions_file=definitions_default,
    )


def ensure_tools_hook_file_interactive(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Prompt for hook tool source when hook injection is enabled and config is missing."""
    if inject_via(config) != "hook" or not tools_hook_file_missing(config):
        return config
    if not sys.stdin.isatty():
        return config

    if tools_hook_tools_from(config) == "executor":
        prompt_target = "Executor URL is not configured"
    else:
        prompt_target = f"Tools definitions file {resolved_tools_hook_file(config)} is missing"
    if not _prompt_yes_no(f"{prompt_target}. Configure now?", default_yes=True):
        return config
    tools_overlay = prompt_tools_hook_config(config, context="launch", inject_mode="hook")
    overlay: dict[str, Any] = {
        "pruning": {"inject_via": "hook", "tools": tools_overlay},
    }
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        return load_config(config_path)
    return config
