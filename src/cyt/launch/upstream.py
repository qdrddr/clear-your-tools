"""Upstream resolution, prompts, and CLI persistence for launch and proxy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.common.agents import LAUNCH_AGENTS, AgentName, unknown_launch_agent_message

__all__ = [
    "LAUNCH_AGENTS",
    "AgentName",
    "apply_upstream_cli",
    "build_upstream_overlay",
    "compatible_upstreams",
    "direct_upstream_base_url",
    "ensure_upstream_for_runtime",
    "filter_upstreams_by_agent",
    "filter_upstreams_for_launch",
    "format_upstream_option",
    "infer_upstream_kind_from_agent",
    "infer_upstream_kind_from_url",
    "launch_agent_for_upstream_kind",
    "list_upstreams",
    "log_auto_selected_upstream",
    "parse_agent_name",
    "persist_upstream_overlay",
    "prompt_confirm_default_upstream",
    "prompt_upstream_picker",
    "prompt_upstream_setup",
    "resolve_upstream_kind",
    "select_upstream_endpoint",
    "unknown_launch_agent_message",
    "upstream_endpoint_name",
]
from cyt.config import (
    load_config,
    load_user_config_overlay,
    resolve_config_path,
    save_user_config,
    upstream_url_defaults,
)
from cyt.proxy.setup_wizard import (
    UPSTREAM_KIND_ALIASES,
    apply_upstream_cli_to_config,
    derive_upstream_name_from_url,
    merge_setup_overlay,
    normalize_upstream_kind,
    normalize_upstream_url,
    prompt_required,
    prompt_with_default,
    upstream_entry_endpoint,
)

_AGENT_DEFAULT_URLS: dict[Literal["claude", "codex"], tuple[str, str]] = {
    "claude": ("https://api.anthropic.com", "anthropic"),
    "codex": ("https://api.openai.com", "openai"),
}

_AGENT_KIND: dict[Literal["claude", "codex"], str] = {
    "claude": "anthropic",
    "codex": "openai",
}


def _reverse_section(config: dict[str, Any]) -> dict[str, Any]:
    network = config.get("network")
    if not isinstance(network, dict):
        return {}
    proxy = network.get("proxy")
    if not isinstance(proxy, dict):
        return {}
    reverse = proxy.get("reverse")
    return reverse if isinstance(reverse, dict) else {}


def list_upstreams(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return upstream entries from a user config overlay."""
    upstreams = _reverse_section(config).get("upstreams", [])
    if not isinstance(upstreams, list):
        return []
    return [entry for entry in upstreams if isinstance(entry, dict)]


def infer_upstream_kind_from_url(url: str) -> str | None:
    """Return canonical kind when *url* matches a bundled default upstream URL."""
    normalized = normalize_upstream_url(url)
    for kind, default_url in upstream_url_defaults().items():
        if kind in ("anthropic", "openai") and normalize_upstream_url(default_url) == normalized:
            return kind
    return None


def parse_agent_name(raw: str) -> AgentName:
    """Return a validated launch agent name."""
    lowered = raw.lower()
    for name in LAUNCH_AGENTS:
        if lowered == name:
            return name
    raise ValueError(unknown_launch_agent_message(raw))


def infer_upstream_kind_from_agent(agent: AgentName) -> str:
    """Map launch agent name to upstream kind."""
    if agent == "cursor":
        raise ValueError("cursor has no upstream kind")
    return _AGENT_KIND[agent]


def launch_agent_for_upstream_kind(kind: str | None) -> Literal["claude", "codex"] | None:
    """Map reverse-proxy upstream kind to the launch agent used for inject_via checks."""
    if kind is None:
        return None
    normalized = normalize_upstream_kind(kind)
    if normalized == "anthropic":
        return "claude"
    if normalized == "openai":
        return "codex"
    return None


def resolve_upstream_kind(
    url: str | None,
    *,
    agent: AgentName | None,
    explicit: str | None,
) -> str | None:
    """Resolve upstream kind from CLI flag, canonical URL, or agent default."""
    if explicit is not None:
        return normalize_upstream_kind(explicit)
    if url is not None:
        if inferred := infer_upstream_kind_from_url(url):
            return inferred
    if agent is not None:
        if agent == "cursor":
            return None
        return infer_upstream_kind_from_agent(agent)
    return None


def filter_upstreams_by_agent(
    upstreams: list[dict[str, Any]],
    agent: AgentName,
) -> list[dict[str, Any]]:
    """Return upstream entries whose kind matches *agent* (claude→anthropic, codex→openai)."""
    expected = infer_upstream_kind_from_agent(agent)
    result: list[dict[str, Any]] = []
    for entry in upstreams:
        kind_raw = entry.get("kind")
        if kind_raw is None:
            continue
        try:
            kind = normalize_upstream_kind(str(kind_raw))
        except ValueError:
            continue
        if kind == expected:
            result.append(entry)
    return result


def _is_non_canonical_upstream_entry(entry: dict[str, Any]) -> bool:
    url = str(entry.get("url") or entry.get("host_url") or entry.get("base_url") or "")
    if not url.strip():
        return True
    return infer_upstream_kind_from_url(normalize_upstream_url(url)) is None


def filter_upstreams_for_launch(
    upstreams: list[dict[str, Any]],
    agent: AgentName,
) -> list[dict[str, Any]]:
    """Return upstream entries selectable for *agent* launch (includes compatible gateways)."""
    expected = infer_upstream_kind_from_agent(agent)
    result: list[dict[str, Any]] = []
    for entry in upstreams:
        kind_raw = entry.get("kind")
        if kind_raw is None:
            continue
        try:
            kind = normalize_upstream_kind(str(kind_raw))
        except ValueError:
            continue
        if kind == expected:
            result.append(entry)
        elif agent == "claude" and kind == "openai" and _is_non_canonical_upstream_entry(entry):
            # Gateways such as OpenRouter use openai kind but serve Claude via the proxy.
            result.append(entry)
    return result


def compatible_upstreams(
    config: dict[str, Any],
    agent: AgentName,
) -> list[dict[str, Any]]:
    """Return upstream entries compatible with *agent*."""
    return filter_upstreams_for_launch(list_upstreams(config), agent)


def upstream_endpoint_name(entry: dict[str, Any]) -> str:
    name = upstream_entry_endpoint(entry)
    if name == "?":
        raise ValueError("Upstream entry missing endpoint name")
    return name


def format_upstream_option(entry: dict[str, Any]) -> str:
    """Human-readable upstream label for logs and prompts."""
    return f"{upstream_endpoint_name(entry)} ({entry.get('url', '?')})"


def log_auto_selected_upstream(entry: dict[str, Any], *, agent: AgentName) -> None:
    """Log a non-interactive upstream choice for this launch."""
    kind = infer_upstream_kind_from_agent(agent)
    print(
        f"Auto-selected upstream for {agent} (kind={kind}): {format_upstream_option(entry)}",
        file=sys.stderr,
    )


def select_upstream_endpoint(
    upstreams: list[dict[str, Any]],
    *,
    agent: AgentName,
    label: str,
) -> str:
    """Pick an upstream endpoint, auto-selecting when only one matches *agent*."""
    compatible = filter_upstreams_for_launch(upstreams, agent)
    if len(compatible) == 1:
        log_auto_selected_upstream(compatible[0], agent=agent)
        return upstream_endpoint_name(compatible[0])

    if len(compatible) >= 2:
        if not sys.stdin.isatty():
            names = ", ".join(upstream_endpoint_name(entry) for entry in compatible)
            raise SystemExit(
                f"Multiple {agent}-compatible upstreams configured ({names}). "
                "Pass --endpoint NAME.",
            )
        return prompt_upstream_picker(compatible, label=label, agent=agent)

    raise SystemExit(
        f"No {agent}-compatible upstream configured. "
        "Run once interactively or pass --upstream / --endpoint.",
    )


def prompt_upstream_picker(
    upstreams: list[dict[str, Any]],
    *,
    label: str,
    agent: AgentName | None = None,
) -> str:
    """Prompt when multiple compatible upstreams exist; return endpoint name."""
    if len(upstreams) == 1:
        if agent is not None:
            log_auto_selected_upstream(upstreams[0], agent=agent)
        return upstream_endpoint_name(upstreams[0])

    options = [format_upstream_option(entry) for entry in upstreams]
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}", file=sys.stderr)
    while True:
        raw = prompt_with_default(label, "1")
        try:
            choice = int(raw) - 1
            if 0 <= choice < len(upstreams):
                return upstream_endpoint_name(upstreams[choice])
        except ValueError:
            pass
        print(f"Choose 1-{len(upstreams)}.", file=sys.stderr)


def prompt_confirm_default_upstream(agent: AgentName) -> tuple[str, str, str]:
    """One-line confirm for launch when no compatible upstream is configured."""
    if agent == "cursor":
        raise ValueError("cursor has no default upstream")
    default_url, kind = _AGENT_DEFAULT_URLS[agent]
    raw = prompt_with_default(f"Add upstream for {agent}?", default_url)
    url = normalize_upstream_url(raw or default_url)
    endpoint = derive_upstream_name_from_url(url)
    return url, kind, endpoint


def prompt_upstream_setup() -> tuple[str, str, str]:
    """Interactive upstream setup for bare ``cyt proxy`` with no config."""
    defaults = upstream_url_defaults()
    print(
        "Configure upstream API endpoint (kind + URL).\n"
        f"Common URLs: anthropic={defaults.get('anthropic', '')}, "
        f"openai={defaults.get('openai', '')}",
        file=sys.stderr,
    )
    url = normalize_upstream_url(
        prompt_required("Upstream URL"),
    )
    inferred = infer_upstream_kind_from_url(url)
    default_endpoint = derive_upstream_name_from_url(url)
    endpoint = prompt_with_default("Endpoint name", default_endpoint).strip() or default_endpoint
    if inferred is not None:
        print(
            f"Upstream kind derived from URL {url}: kind={inferred}",
            file=sys.stderr,
        )
        kind = inferred
    else:
        allowed = ", ".join(["anthropic", "openai", *sorted(UPSTREAM_KIND_ALIASES)])
        kind = normalize_upstream_kind(
            prompt_required(
                f"Upstream kind ({allowed})",
            ),
        )
    return url, kind, endpoint


def build_upstream_overlay(
    *,
    url: str,
    kind: str,
    endpoint: str,
) -> dict[str, Any]:
    """Build a minimal reverse-proxy overlay for a single upstream."""
    normalized_kind = normalize_upstream_kind(kind)
    upstream_entry = {
        "endpoint": endpoint,
        "kind": normalized_kind,
        "url": normalize_upstream_url(url),
    }
    return {
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [upstream_entry],
                    "endpoints": [endpoint],
                },
            },
        },
    }


def persist_upstream_overlay(config_path: Path | None, overlay: dict[str, Any]) -> None:
    """Merge *overlay* into the user config at *config_path*."""
    path = resolve_config_path(config_path)
    existing = load_user_config_overlay(path)
    merged = merge_setup_overlay(existing, overlay)
    save_user_config(path, merged, apply_bundled_sections=False)


def apply_upstream_cli(
    config_path: Path | None,
    *,
    upstream_url: str,
    upstream_kind: str,
    upstream_name: str | None = None,
) -> str:
    """Persist CLI upstream settings; return endpoint name."""
    return apply_upstream_cli_to_config(
        resolve_config_path(config_path),
        upstream_url=upstream_url,
        upstream_kind=upstream_kind,
        upstream_name=upstream_name,
    )


def ensure_upstream_for_runtime(
    *,
    agent: AgentName | None,
    config_path: Path | None,
    upstream_url: str | None,
    upstream_kind: str | None,
    upstream_name: str | None,
) -> str | None:
    """Resolve upstream configuration; return CLI-applied endpoint name if any."""
    if agent == "cursor":
        return None

    path = resolve_config_path(config_path)
    overlay = load_user_config_overlay(path)

    if upstream_url is not None:
        kind = resolve_upstream_kind(
            upstream_url,
            agent=agent,
            explicit=upstream_kind,
        )
        if kind is None:
            raise SystemExit(
                "Cannot infer upstream kind from URL. "
                "Pass --upstream-kind or use a canonical URL "
                "(https://api.openai.com or https://api.anthropic.com).",
            )
        return apply_upstream_cli(
            path,
            upstream_url=upstream_url,
            upstream_kind=kind,
            upstream_name=upstream_name,
        )

    if agent is not None:
        compatible = compatible_upstreams(overlay, agent)
        if compatible:
            return None
        if not sys.stdin.isatty():
            raise SystemExit(
                f"No {agent}-compatible upstream configured. "
                "Run interactively once or pass --upstream URL.",
            )
        url, kind, endpoint = prompt_confirm_default_upstream(agent)
        persist_upstream_overlay(
            path,
            build_upstream_overlay(url=url, kind=kind, endpoint=endpoint),
        )
        return endpoint

    if list_upstreams(overlay):
        return None

    if list_upstreams(load_config(path)):
        return None

    if not sys.stdin.isatty():
        raise SystemExit(
            "No upstream configured. Run interactively once or pass --upstream URL.",
        )
    url, kind, endpoint = prompt_upstream_setup()
    persist_upstream_overlay(
        path,
        build_upstream_overlay(url=url, kind=kind, endpoint=endpoint),
    )
    return endpoint


def direct_upstream_base_url(config: dict[str, Any], endpoint: str) -> str:
    """Return the direct upstream base URL when launch skips the reverse proxy."""
    from cyt.launch.upstream_credentials import upstream_for_endpoint
    from cyt.proxy.setup_wizard import normalize_upstream_url

    entry = upstream_for_endpoint(config, endpoint)
    if entry is not None:
        url = entry.get("url")
        if isinstance(url, str) and url.strip():
            return normalize_upstream_url(url)
    defaults = upstream_url_defaults()
    if endpoint in defaults:
        return defaults[endpoint]
    kind = entry.get("kind") if isinstance(entry, dict) else None
    if isinstance(kind, str) and kind in defaults:
        return defaults[kind]
    return defaults.get("anthropic", "https://api.anthropic.com")
