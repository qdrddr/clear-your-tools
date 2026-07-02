"""Interactive wizard helpers for tools hook injection configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import (
    DEFAULT_TOOLS_HOOK_MCP_CLIENT_FILE,
    DEFAULT_TOOLS_HOOK_MCP_DEFINITIONS_FILE,
    DEFAULT_TOOLS_HOOK_TOOLS_FROM,
    DEFAULT_TOOLS_INJECT_VIA,
    load_config,
    resolved_tools_hook_file,
    save_user_config,
    tools_hook_file_missing,
    tools_inject_via,
)
from cyt.proxy.setup import _prompt, _prompt_choice, _prompt_yes_no

ToolsSetupContext = Literal["hook", "setup", "launch"]

_INJECT_VIA_CHOICES = ("proxy", "hook")
_TOOLS_FROM_CHOICES = ("client", "definitions")


def build_tools_hook_config_overlay(
    *,
    inject_via: str,
    tools_from: str,
    mcp_client_file: str,
    mcp_definitions_file: str,
) -> dict[str, Any]:
    return {
        "tools": {
            "inject_via": inject_via,
            "hook": {
                "tools_from": tools_from,
                "mcp_client_file": mcp_client_file,
                "mcp_definitions_file": mcp_definitions_file,
            },
        },
    }


def prompt_tools_hook_config(
    existing: dict[str, Any],
    *,
    context: ToolsSetupContext,
) -> dict[str, Any]:
    """Prompt for tools hook settings; return pruning.tools overlay fragment."""
    pruning = existing.get("pruning")
    pruning_cfg = pruning if isinstance(pruning, dict) else {}
    tools_cfg = pruning_cfg.get("tools")
    tools = tools_cfg if isinstance(tools_cfg, dict) else {}
    hook_cfg = tools.get("hook")
    hook = hook_cfg if isinstance(hook_cfg, dict) else {}

    print("\n--- Tool hook injection ---")

    current_inject = str(tools.get("inject_via", DEFAULT_TOOLS_INJECT_VIA)).strip().lower()
    if context == "hook":
        inject_default = "hook"
    else:
        inject_default = current_inject if current_inject in _INJECT_VIA_CHOICES else "proxy"
    inject_via = _prompt_choice(
        "Tools inject via (hook | proxy)",
        list(_INJECT_VIA_CHOICES),
        default_index=_INJECT_VIA_CHOICES.index(inject_default),
    )

    current_from = str(hook.get("tools_from", DEFAULT_TOOLS_HOOK_TOOLS_FROM)).strip().lower()
    if context == "hook":
        from_default = "client"
    else:
        from_default = current_from if current_from in _TOOLS_FROM_CHOICES else "client"

    if inject_via == "hook":
        tools_from = _prompt_choice(
            "Tool catalog source (client | definitions)",
            list(_TOOLS_FROM_CHOICES),
            default_index=_TOOLS_FROM_CHOICES.index(from_default),
        )
        client_default = str(
            hook.get("mcp_client_file", DEFAULT_TOOLS_HOOK_MCP_CLIENT_FILE),
        )
        definitions_default = str(
            hook.get("mcp_definitions_file", DEFAULT_TOOLS_HOOK_MCP_DEFINITIONS_FILE),
        )
        if tools_from == "definitions":
            path_text = _prompt("MCP definitions file", definitions_default)
        else:
            path_text = _prompt("MCP client config file", client_default)
        path_text = str(Path(path_text).expanduser())
        if not Path(path_text).is_file():
            print(
                f"Note: {path_text} does not exist yet; hook will skip tool injection until it does.",
                file=sys.stderr,
            )
        if tools_from == "definitions":
            mcp_definitions_file = path_text
            mcp_client_file = client_default
        else:
            mcp_client_file = path_text
            mcp_definitions_file = definitions_default
    else:
        tools_from = from_default
        mcp_client_file = str(hook.get("mcp_client_file", DEFAULT_TOOLS_HOOK_MCP_CLIENT_FILE))
        mcp_definitions_file = str(
            hook.get("mcp_definitions_file", DEFAULT_TOOLS_HOOK_MCP_DEFINITIONS_FILE),
        )

    return build_tools_hook_config_overlay(
        inject_via=inject_via,
        tools_from=tools_from,
        mcp_client_file=mcp_client_file,
        mcp_definitions_file=mcp_definitions_file,
    )


def ensure_tools_hook_file_interactive(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Prompt for missing catalog path on launch; persist overlay when changed."""
    if tools_inject_via(config) != "hook" or not tools_hook_file_missing(config):
        return config

    missing_path = resolved_tools_hook_file(config)
    print(
        f"\nTools hook is enabled but catalog file is missing: {missing_path}",
        file=sys.stderr,
    )
    if not _prompt_yes_no("Configure tools catalog path now?", default_yes=True):
        return config

    overlay = {"pruning": prompt_tools_hook_config(config, context="launch")}
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        print(f"Updated {config_path}", file=sys.stderr)
    return load_config(config_path)
