"""Shared runtime bootstrap for ``cyt proxy`` and ``cyt launch``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    load_proxy_env,
    load_user_config_overlay,
    resolve_config_path,
    resolve_reverse_port,
)
from cyt.launch.secrets import ensure_proxy_pipeline_credentials, ensure_runtime_credentials
from cyt.launch.upstream import AgentName, ensure_upstream_for_runtime, list_upstreams
from cyt.proxy.setup_wizard import upstream_entry_endpoint
from cyt.pruners.remote import PrunerSettingsCache

_BM25_FALLBACK_MESSAGE = (
    "No pruner pipeline configured: fallback to BM25. "
    "Please run to configure more advanced pruning:\n"
    "  cyt setup"
)


def _apply_bm25_fallback_pipeline(config: dict[str, Any], *, quiet: bool = False) -> None:
    if not quiet:
        import sys

        print(_BM25_FALLBACK_MESSAGE, file=sys.stderr)
    pruning = config.setdefault("pruning", {})
    if isinstance(pruning, dict):
        tools = pruning.setdefault("tools", {})
        if isinstance(tools, dict):
            tools["sequence"] = ["bm25"]


def _apply_bm25_fallback_if_needed(
    config: dict[str, Any],
    config_path: Path,
    *,
    upstream_cli: bool,
    quiet: bool = False,
) -> None:
    from cyt.config import (
        load_user_config_overlay,
        missing_proxy_env_var_names,
        remote_pruning_pipeline_configured,
    )

    if upstream_cli:
        if not missing_proxy_env_var_names(config):
            return
        _apply_bm25_fallback_pipeline(config, quiet=quiet)
        return

    user_config = load_user_config_overlay(config_path)
    if remote_pruning_pipeline_configured(user_config):
        return
    _apply_bm25_fallback_pipeline(config, quiet=quiet)


@dataclass
class RuntimeContext:
    config: dict[str, Any]
    config_path: Path
    port: int
    credential_sources: dict[str, str]
    upstream_endpoint: str | None
    upstream_url: str | None
    pruner_settings: PrunerSettingsCache | None = None


def prepare_runtime(
    *,
    agent: AgentName | None,
    config_path: Path | None,
    port: int | None,
    upstream_url: str | None,
    upstream_kind: str | None,
    upstream_name: str | None,
    resolve_credentials: bool = True,
) -> RuntimeContext:
    """Load env, resolve upstream, credentials, and merged config."""
    load_proxy_env()
    path = resolve_config_path(config_path)
    upstream_endpoint = ensure_upstream_for_runtime(
        agent=agent,
        config_path=path,
        upstream_url=upstream_url,
        upstream_kind=upstream_kind,
        upstream_name=upstream_name,
    )
    config = load_config(path)
    upstream_cli = upstream_url is not None
    resolved_port = resolve_reverse_port(config, port)
    credential_sources: dict[str, str] = {}
    pruner_settings: PrunerSettingsCache | None = None
    if resolve_credentials:
        if agent is None:
            # Resolve tool/skills pruner keys (shell env → .env → keyring → prompt) and
            # warm remote pruner clients before any BM25 fallback or request handling.
            pruner_settings = ensure_proxy_pipeline_credentials(
                config,
                credential_sources=credential_sources,
            )
        else:
            ensure_runtime_credentials(
                config,
                agent=agent,
                credential_sources=credential_sources,
            )
    elif agent is None:
        from cyt.launch.secrets import build_pruner_settings_cache

        pruner_settings = build_pruner_settings_cache(config)
    _apply_bm25_fallback_if_needed(
        config,
        path,
        upstream_cli=upstream_cli,
        quiet=agent is not None,
    )

    resolved_upstream_url = upstream_url
    if resolved_upstream_url is None and upstream_endpoint is not None:
        for entry in list_upstreams(load_user_config_overlay(path)):
            if upstream_entry_endpoint(entry) == upstream_endpoint:
                resolved_upstream_url = (
                    str(
                        entry.get("url") or entry.get("host_url") or entry.get("base_url") or "",
                    )
                    or None
                )
                break

    return RuntimeContext(
        config=config,
        config_path=path,
        port=resolved_port,
        credential_sources=credential_sources,
        upstream_endpoint=upstream_endpoint,
        upstream_url=resolved_upstream_url,
        pruner_settings=pruner_settings,
    )
