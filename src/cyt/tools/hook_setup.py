"""Interactive wizard helpers for tools hook injection configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import (
    _default_at,
    inject_via_agents,
    inject_via_for_agent,
    load_config,
    save_user_config,
    tools_hook_cloudflare_url,
    tools_hook_cyt_mcp_agent,
    tools_hook_executor_url,
    tools_hook_file_missing,
    tools_hook_mcp_definitions_file,
    tools_hook_sources,
)
from cyt.proxy.setup_wizard import _prompt, _prompt_yes_no

__all__ = [
    "sys",
]

ToolsSetupContext = Literal["hook", "setup", "launch"]

_TOOLS_FROM_CHOICES = ("cyt_mcp", "mcpc", "cloudflare", "executor", "definitions")


def _hook_from_default(current_from: str, *, context: ToolsSetupContext) -> str:
    if context == "hook":
        return "cyt_mcp"
    if current_from in {"client", "executor"}:
        return "executor"
    if current_from == "cyt_mcp":
        return "cyt_mcp"
    if current_from == "mcpc":
        return "mcpc"
    if current_from in _TOOLS_FROM_CHOICES:
        return current_from
    return "cyt_mcp"


def _parse_selected_hook_sources(raw_sources: str, from_default: str) -> list[str]:
    selected: list[str] = []
    for item in raw_sources.split(","):
        choice = item.strip().lower()
        if choice in {"client", "executor"}:
            choice = "executor"
        if choice in _TOOLS_FROM_CHOICES and choice not in selected:
            selected.append(choice)
    if selected:
        return selected
    return [from_default if from_default in _TOOLS_FROM_CHOICES else "cyt_mcp"]


def _prompt_hook_source_paths(
    selected: list[str],
    *,
    executor_default: str,
    definitions_default: str,
    cloudflare_default: str,
) -> tuple[str, str, str]:
    executor_url = executor_default
    definitions_path = definitions_default
    cloudflare_url = cloudflare_default
    if "definitions" in selected:
        path_text = _prompt("MCP definitions file", definitions_default)
        path_text = str(Path(path_text).expanduser())
        if not Path(path_text).is_file():
            print(
                f"Note: {path_text} does not exist yet; hook will skip that source until it does.",
                file=sys.stderr,
            )
        definitions_path = path_text
    if "executor" in selected:
        url_text = _prompt("Executor base URL", executor_default).strip().rstrip("/")
        if not url_text:
            print(
                "Note: executor URL is empty; hook will skip that source until configured.",
                file=sys.stderr,
            )
        executor_url = url_text
    if "cloudflare" in selected:
        url_text = _prompt("Cloudflare MCP portal URL", cloudflare_default).strip().rstrip("/")
        if not url_text:
            print(
                "Note: cloudflare URL is empty; hook will skip that source until configured.",
                file=sys.stderr,
            )
        cloudflare_url = url_text
    return executor_url, definitions_path, cloudflare_url


def build_tools_hook_config_overlay(
    *,
    tools_from: list[str],
    executor_url: str,
    mcp_definitions_file: str,
    cloudflare_url: str = "",
    executor_token_var: str | None = None,
    cyt_mcp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a ``pruning.tools`` overlay fragment (``hook`` settings only)."""
    hook: dict[str, Any] = {
        "tools_from": tools_from,
        "executor_url": executor_url,
        "mcp_definitions_file": mcp_definitions_file,
        "cloudflare_url": cloudflare_url,
    }
    if executor_token_var:
        hook["executor_token_var"] = executor_token_var
    if cyt_mcp:
        hook["cyt_mcp"] = cyt_mcp
    return {"hook": hook}


def build_pruning_tools_hook_save_overlay(
    *,
    tools_from: list[str],
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


def _tools_from_overlay_value(sources: tuple[str, ...], *, fallback: str) -> list[str]:
    if sources:
        return list(sources)
    return [fallback]


def prompt_tools_hook_config(
    existing: dict[str, Any],
    *,
    context: ToolsSetupContext,
    inject_mode: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Prompt for tools hook settings; return pruning.tools overlay fragment."""
    pruning = existing.get("pruning")
    pruning_cfg = pruning if isinstance(pruning, dict) else {}
    tools_cfg = pruning_cfg.get("tools")
    tools = tools_cfg if isinstance(tools_cfg, dict) else {}
    hook_cfg = tools.get("hook")
    hook = hook_cfg if isinstance(hook_cfg, dict) else {}

    active_inject = inject_mode or inject_via_for_agent(
        existing,
        tools_hook_cyt_mcp_agent(existing),
    )
    if context == "hook":
        active_inject = "hook"

    if active_inject == "hook":
        print("\n--- MCP aggregator ---")
    else:
        print("\n--- Tool hook injection ---")

    existing_sources = tools_hook_sources(existing)
    bundled_tools_from = _default_at("pruning", "tools", "hook", "tools_from")
    if isinstance(bundled_tools_from, str):
        bundled_tools_from = [bundled_tools_from]
    current_from = existing_sources[0] if existing_sources else str(bundled_tools_from[0])
    from_default = _hook_from_default(current_from, context=context)

    executor_default = str(hook.get("executor_url") or tools_hook_executor_url(load_config()))
    definitions_default = str(
        hook.get("mcp_definitions_file") or tools_hook_mcp_definitions_file(load_config()),
    )
    cloudflare_default = str(hook.get("cloudflare_url") or tools_hook_cloudflare_url(load_config()))

    if active_inject == "hook":
        print(
            "Configure tool catalog sources "
            "(comma-separated: cyt_mcp, mcpc, cloudflare, executor, definitions).",
        )
        raw_sources = _prompt(
            "Tool catalog sources",
            ",".join(tools_hook_sources(existing) or [from_default]),
        )
        selected = _parse_selected_hook_sources(raw_sources, from_default)
        cyt_mcp_overlay: dict[str, Any] | None = None
        if "cyt_mcp" in selected:
            from cyt.hook.cli_invocation import detect_hook_cli_invocation
            from cyt.tools.cyt_mcp_setup import (
                cyt_mcp_hook_settings_overlay,
                prompt_cyt_mcp_transport,
                setup_cyt_mcp_for_agent,
                write_agent_cyt_mcp_entry,
            )

            launch_agent = (
                (agent or "").strip() or os.environ.get("CYT_LAUNCH_AGENT", "").strip() or "cursor"
            )
            transport = prompt_cyt_mcp_transport()
            cyt_mcp_overlay = cyt_mcp_hook_settings_overlay(
                transport=transport,
                agent=launch_agent,
            )
            invocation = detect_hook_cli_invocation()
            if invocation.is_dev and invocation.repo_root is not None:
                print(
                    f"\nInstalling development cyt-mcp via uv run --directory {invocation.repo_root}",
                    file=sys.stderr,
                )
                write_agent_cyt_mcp_entry(
                    launch_agent,
                    invocation=invocation,
                    transport=transport,
                )
            print(f"\n--- Migrate ({launch_agent})'s MCP config ---")
            if _prompt_yes_no("Migrate agent MCP config to cyt-mcp aggregator?", default_yes=True):
                setup_cyt_mcp_for_agent(
                    launch_agent,
                    invocation=invocation,
                    transport=transport,
                    verify_only=False,
                )
            elif not (invocation.is_dev and invocation.repo_root is not None):
                write_agent_cyt_mcp_entry(
                    launch_agent,
                    invocation=invocation,
                    transport=transport,
                )
        tools_from = selected
        executor_default, definitions_default, cloudflare_default = _prompt_hook_source_paths(
            selected,
            executor_default=executor_default,
            definitions_default=definitions_default,
            cloudflare_default=cloudflare_default,
        )
        return build_tools_hook_config_overlay(
            tools_from=tools_from,
            executor_url=executor_default,
            mcp_definitions_file=definitions_default,
            cloudflare_url=cloudflare_default,
            cyt_mcp=cyt_mcp_overlay,
        )

    tools_from = _tools_from_overlay_value(existing_sources, fallback=from_default)
    return build_tools_hook_config_overlay(
        tools_from=tools_from,
        executor_url=executor_default,
        mcp_definitions_file=definitions_default,
        cloudflare_url=cloudflare_default,
    )


def ensure_tools_hook_file_interactive(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Prompt for hook tool source when hook injection is enabled and config is missing."""
    if inject_via_for_agent(
        config,
        tools_hook_cyt_mcp_agent(config),
    ) != "hook" or not tools_hook_file_missing(config):
        return config
    if not sys.stdin.isatty():
        return config

    sources = tools_hook_sources(config)
    prompt_target = "Tools hook sources are not fully configured"
    if len(sources) == 1 and sources[0] == "executor":
        prompt_target = "Executor URL is not configured"
    elif len(sources) == 1 and sources[0] == "cyt_mcp":
        from cyt.cyt_mcp.readiness import report_cyt_mcp_hook_readiness

        report_cyt_mcp_hook_readiness(config)
        return config
    elif len(sources) == 1 and sources[0] == "mcpc":
        from cyt.mcpc.readiness import report_mcpc_hook_readiness

        report_mcpc_hook_readiness(config)
        return config
    elif len(sources) == 1 and sources[0] == "cloudflare":
        if tools_hook_cloudflare_url(config):
            from cyt.cloudflare.readiness import report_cloudflare_hook_readiness

            report_cloudflare_hook_readiness(config)
            return config
        prompt_target = "Cloudflare portal URL is not configured"
    if not _prompt_yes_no(f"{prompt_target}. Configure now?", default_yes=True):
        return config
    tools_overlay = prompt_tools_hook_config(config, context="launch", inject_mode="hook")
    overlay: dict[str, Any] = {
        "pruning": {
            "inject_via": dict.fromkeys(inject_via_agents(), "hook"),
            "tools": tools_overlay,
        },
    }
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        return load_config(config_path)
    return config
