"""Upstream resolution, prompts, and CLI persistence for launch and proxy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import (
    UPSTREAM_URL_DEFAULTS,
    load_config,
    load_user_config_overlay,
    resolve_config_path,
    save_user_config,
)
from cyt.proxy.setup import (
    UPSTREAM_KIND_ALIASES,
    apply_upstream_cli_to_config,
    derive_upstream_name_from_url,
    merge_setup_overlay,
    normalize_upstream_kind,
    normalize_upstream_url,
    prompt_required,
    prompt_with_default,
)

AgentName = Literal["claude", "codex"]

_AGENT_DEFAULT_URLS: dict[AgentName, tuple[str, str]] = {
    "claude": ("https://api.anthropic.com", "anthropic"),
    "codex": ("https://api.openai.com", "openai"),
}

_AGENT_KIND: dict[AgentName, str] = {
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
    for kind, default_url in UPSTREAM_URL_DEFAULTS.items():
        if kind in ("anthropic", "openai") and normalize_upstream_url(default_url) == normalized:
            return kind
    return None


def parse_agent_name(raw: str) -> AgentName:
    """Return a validated launch agent name."""
    agent = raw.lower()
    if agent == "claude":
        return "claude"
    if agent == "codex":
        return "codex"
    raise ValueError(f"Unknown agent {raw!r}; expected claude or codex")


def infer_upstream_kind_from_agent(agent: AgentName) -> str:
    """Map launch agent name to upstream kind."""
    return _AGENT_KIND[agent]


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


def compatible_upstreams(
    config: dict[str, Any],
    agent: AgentName,
) -> list[dict[str, Any]]:
    """Return upstream entries compatible with *agent*."""
    return filter_upstreams_by_agent(list_upstreams(config), agent)


def upstream_endpoint_name(entry: dict[str, Any]) -> str:
    name = entry.get("upstream")
    if not name:
        raise ValueError("Upstream entry missing upstream name")
    return str(name)


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
    compatible = filter_upstreams_by_agent(upstreams, agent)
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
    default_url, kind = _AGENT_DEFAULT_URLS[agent]
    raw = prompt_with_default(f"Add upstream for {agent}?", default_url)
    url = normalize_upstream_url(raw or default_url)
    endpoint = derive_upstream_name_from_url(url)
    return url, kind, endpoint


def prompt_upstream_setup() -> tuple[str, str, str]:
    """Interactive upstream setup for bare ``cyt proxy`` with no config."""
    print(
        "Configure upstream API endpoint (kind + URL).\n"
        f"Common URLs: anthropic={UPSTREAM_URL_DEFAULTS['anthropic']}, "
        f"openai={UPSTREAM_URL_DEFAULTS['openai']}",
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
        "upstream": endpoint,
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
