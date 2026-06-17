"""Resolve and persist API credentials for non-canonical reverse-proxy upstreams."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    load_user_config_overlay,
    provider_registry,
    resolve_config_path,
    save_user_config,
)
from cyt.launch.secrets import ensure_named_credentials
from cyt.launch.upstream import infer_upstream_kind_from_url, list_upstreams
from cyt.proxy.setup import (
    _extract_hostname,
    _prompt_key_var_name,
    _prompt_required,
    derive_second_level_domain_from_hostname,
    derive_upstream_name_from_url,
    merge_setup_overlay,
    normalize_upstream_url,
    upstream_entry_endpoint,
)

_CLAUDE_UPSTREAM_AUTH_FALLBACKS: tuple[str, ...] = ()


def upstream_entry_url(entry: dict[str, Any]) -> str:
    """Return the upstream base URL from a reverse-proxy upstream entry."""
    return normalize_upstream_url(
        str(entry.get("url") or entry.get("host_url") or entry.get("base_url") or ""),
    )


def is_canonical_upstream(entry: dict[str, Any]) -> bool:
    """True when *entry* points at a bundled default Anthropic or OpenAI API URL."""
    url = upstream_entry_url(entry)
    return bool(url) and infer_upstream_kind_from_url(url) is not None


def _provider_nick_from_upstream_entry(
    config: dict[str, Any],
    entry: dict[str, Any],
) -> str | None:
    """Return ``provider_nick`` only when explicitly set on the upstream entry."""
    del config  # registry heuristics require an explicit upstream link
    raw = entry.get("provider_nick")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return None


def _key_var_from_provider_nick(config: dict[str, Any], provider_nick: str) -> str | None:
    registry = provider_registry(config)
    provider_entry = registry.get(provider_nick)
    if not provider_entry:
        return None
    key_var = provider_entry.get("key_var_name")
    if key_var is None or not str(key_var).strip():
        return None
    return str(key_var).strip()


def lookup_upstream_key_var(config: dict[str, Any], entry: dict[str, Any]) -> str | None:
    """Return the API-key env var name for *entry*, or ``None`` when unknown."""
    if is_canonical_upstream(entry):
        kind = infer_upstream_kind_from_url(upstream_entry_url(entry))
        if kind == "anthropic":
            return _key_var_from_provider_nick(config, "anthropic") or "ANTHROPIC_API_KEY"
        if kind == "openai":
            return _key_var_from_provider_nick(config, "openai") or "OPENAI_API_KEY"

    provider_nick = _provider_nick_from_upstream_entry(config, entry)
    if provider_nick:
        return _key_var_from_provider_nick(config, provider_nick)
    return None


def _default_provider_nick(entry: dict[str, Any]) -> str:
    endpoint = upstream_entry_endpoint(entry)
    if endpoint != "?":
        return endpoint
    url = upstream_entry_url(entry)
    if url:
        return derive_upstream_name_from_url(url)
    return "custom"


def _default_domain_match(entry: dict[str, Any]) -> list[str]:
    url = upstream_entry_url(entry)
    if not url:
        return []
    hostname = _extract_hostname(url)
    if not hostname:
        return []
    return [derive_second_level_domain_from_hostname(hostname)]


def _prompt_upstream_provider(entry: dict[str, Any]) -> tuple[str, str, str]:
    """Prompt for provider registry fields; return (provider_nick, provider, key_var)."""
    default_nick = _default_provider_nick(entry)
    provider_nick = _prompt_required("Provider nick", default_nick).strip() or default_nick
    provider = (
        _prompt_required(
            "Provider (https://docs.litellm.ai/docs/providers)",
            provider_nick,
        ).strip()
        or provider_nick
    )
    key_var = _prompt_key_var_name(provider=provider)
    return provider_nick, provider, key_var


def _build_provider_persist_overlay(
    entry: dict[str, Any],
    *,
    provider_nick: str,
    provider: str,
    key_var_name: str,
) -> dict[str, Any]:
    domain_match = _default_domain_match(entry)
    provider_row: dict[str, Any] = {
        "provider_nick": provider_nick,
        "provider": provider,
        "key_var_name": key_var_name,
    }
    if domain_match:
        provider_row["domain_match"] = domain_match

    upstream_row = copy.deepcopy(entry)
    upstream_row["provider_nick"] = provider_nick

    return {
        "models": {"providers": [provider_row]},
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [upstream_row],
                },
            },
        },
    }


def _persist_upstream_provider(
    config_path: Path,
    entry: dict[str, Any],
    *,
    provider_nick: str,
    provider: str,
    key_var_name: str,
) -> None:
    existing = load_user_config_overlay(config_path)
    overlay = _build_provider_persist_overlay(
        entry,
        provider_nick=provider_nick,
        provider=provider,
        key_var_name=key_var_name,
    )
    merged = merge_setup_overlay(existing, overlay)
    save_user_config(config_path, merged, apply_bundled_sections=False)
    entry["provider_nick"] = provider_nick


def ensure_upstream_provider_registry(
    config: dict[str, Any],
    config_path: Path,
    entry: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Ensure *entry* has a provider registry row; return (config, key_var_name)."""
    if is_canonical_upstream(entry):
        key_var = lookup_upstream_key_var(config, entry)
        if key_var is None:
            raise SystemExit("Cannot resolve API key env var for canonical upstream.")
        return config, key_var

    key_var = lookup_upstream_key_var(config, entry)
    if key_var is not None:
        return config, key_var

    if not sys.stdin.isatty():
        endpoint = upstream_entry_endpoint(entry)
        url = upstream_entry_url(entry) or "?"
        raise SystemExit(
            f"Upstream {endpoint!r} ({url}) has no provider API-key mapping in config.yaml.\n"
            "Run `cyt launch` or `cyt proxy` interactively once, or add a "
            "models.providers entry with key_var_name.",
        )

    print(
        f"\nConfigure API credentials for upstream "
        f"{upstream_entry_endpoint(entry)} ({upstream_entry_url(entry) or '?'}).",
        file=sys.stderr,
    )
    provider_nick, provider, key_var = _prompt_upstream_provider(entry)
    _persist_upstream_provider(
        config_path,
        entry,
        provider_nick=provider_nick,
        provider=provider,
        key_var_name=key_var,
    )
    config = load_config(config_path)
    return config, key_var


def _resolve_upstream_credential(
    key_var_name: str,
    *,
    credential_sources: dict[str, str],
    allow_prompt: bool,
    fallback_env_names: tuple[str, ...],
) -> None:
    """Resolve *key_var_name*, optionally borrowing from *fallback_env_names*."""
    from cyt.launch.secrets import resolve_credential

    if key_var_name in credential_sources:
        return

    before = dict(os.environ)
    value, source = resolve_credential(
        key_var_name,
        before_env=before,
        allow_prompt=allow_prompt,
    )
    if value and source:
        os.environ[key_var_name] = value
        credential_sources[key_var_name] = source
        return

    for fallback_name in fallback_env_names:
        fallback_value, fallback_source = resolve_credential(
            fallback_name,
            before_env=before,
            allow_prompt=False,
        )
        if fallback_value and fallback_source:
            os.environ[key_var_name] = fallback_value
            credential_sources[key_var_name] = f"{fallback_source} (via {fallback_name})"
            return

    ensure_named_credentials(
        [key_var_name],
        credential_sources=credential_sources,
        allow_prompt=allow_prompt,
    )


def ensure_upstream_credential(
    key_var_name: str,
    *,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
    fallback_env_names: tuple[str, ...] = _CLAUDE_UPSTREAM_AUTH_FALLBACKS,
) -> None:
    """Resolve *key_var_name* into the process environment (shell env wins)."""
    resolved_allow_prompt = sys.stdin.isatty() if allow_prompt is None else allow_prompt
    _resolve_upstream_credential(
        key_var_name,
        credential_sources=credential_sources,
        allow_prompt=resolved_allow_prompt,
        fallback_env_names=fallback_env_names,
    )


def ensure_upstream_credentials(
    *,
    config: dict[str, Any],
    config_path: Path | None,
    entry: dict[str, Any],
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
) -> dict[str, Any]:
    """One-time provider registry + credential setup for a reverse-proxy upstream."""
    if is_canonical_upstream(entry):
        return config

    path = resolve_config_path(config_path)
    config, key_var = ensure_upstream_provider_registry(config, path, entry)
    ensure_upstream_credential(
        key_var,
        credential_sources=credential_sources,
        allow_prompt=allow_prompt,
    )
    return config


def ensure_non_canonical_upstream_credentials(
    *,
    config: dict[str, Any],
    config_path: Path | None,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
) -> dict[str, Any]:
    """Ensure provider registry rows and credentials for all non-canonical upstreams."""
    for entry in list_upstreams(config):
        if is_canonical_upstream(entry):
            continue
        config = ensure_upstream_credentials(
            config=config,
            config_path=config_path,
            entry=entry,
            credential_sources=credential_sources,
            allow_prompt=allow_prompt,
        )
    return config


def upstream_for_endpoint(config: dict[str, Any], endpoint: str) -> dict[str, Any] | None:
    """Return the reverse-proxy upstream entry for *endpoint*, if configured."""
    reverse = config.get("network", {}).get("proxy", {}).get("reverse", {})
    upstreams = reverse.get("upstreams", [])
    if not isinstance(upstreams, list):
        return None
    for entry in upstreams:
        if isinstance(entry, dict) and upstream_entry_endpoint(entry) == endpoint:
            return entry
    return None
