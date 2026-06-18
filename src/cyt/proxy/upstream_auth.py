"""Request-scoped upstream auth for pruning pipelines inside the reverse proxy."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from cyt.pruners.remote import PrunerSettingsCache


def extract_request_upstream_token(headers: Mapping[str, str]) -> str | None:
    """Return bearer or Anthropic-style API key from inbound proxy request headers."""
    for key, value in headers.items():
        lowered = key.lower()
        if lowered == "authorization":
            scheme, _, token = value.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
        if lowered == "x-api-key" and value.strip():
            return value.strip()
    return None


def resolve_upstream_env_token(
    config: dict[str, Any],
    endpoint_name: str,
) -> str | None:
    """Return the configured upstream API key from the process environment."""
    from cyt.launch.upstream_credentials import (
        is_canonical_upstream,
        lookup_upstream_key_var,
        upstream_for_endpoint,
    )

    upstream = upstream_for_endpoint(config, endpoint_name)
    if upstream is None or is_canonical_upstream(upstream):
        return None
    upstream_key_var = lookup_upstream_key_var(config, upstream)
    if not upstream_key_var:
        return None
    value = os.environ.get(upstream_key_var, "").strip()
    return value or None


def resolve_upstream_auth_token(
    headers: Mapping[str, str],
    *,
    config: dict[str, Any] | None,
    endpoint_name: str,
) -> str | None:
    """Prefer inbound client auth; fall back to the proxy's upstream env key."""
    if token := extract_request_upstream_token(headers):
        return token
    if config is None:
        return None
    return resolve_upstream_env_token(config, endpoint_name)


def prepare_forward_headers(
    headers: Mapping[str, str],
    *,
    config: dict[str, Any] | None,
    endpoint_name: str,
) -> dict[str, str]:
    """Filter hop-by-hop headers and ensure upstream auth for non-canonical routes."""
    from cyt.proxy.transport import filter_headers

    forward = filter_headers(dict(headers))
    if config is None:
        return forward

    from cyt.launch.upstream_credentials import is_canonical_upstream, upstream_for_endpoint

    upstream = upstream_for_endpoint(config, endpoint_name)
    if upstream is None or is_canonical_upstream(upstream):
        return forward

    token = resolve_upstream_auth_token(forward, config=config, endpoint_name=endpoint_name)
    if not token:
        return forward

    cleaned = {
        key: value
        for key, value in forward.items()
        if key.lower() not in {"authorization", "x-api-key"}
    }
    cleaned["Authorization"] = f"Bearer {token}"
    cleaned["x-api-key"] = token
    return cleaned


def _pipeline_pruner_key_vars(config: dict[str, Any]) -> list[str]:
    """Return configured tool/skills pruner API-key env var names."""
    from cyt.config import required_proxy_env_var_names

    return required_proxy_env_var_names(config)


def _endpoint_upstream_key_var(config: dict[str, Any], endpoint_name: str) -> str | None:
    from cyt.launch.upstream_credentials import lookup_upstream_key_var, upstream_for_endpoint

    upstream = upstream_for_endpoint(config, endpoint_name)
    if upstream is None:
        return None
    return lookup_upstream_key_var(config, upstream)


def _provider_nick_for_key_var(config: dict[str, Any], key_var: str) -> str | None:
    from cyt.config import provider_registry

    for nick, entry in provider_registry(config).items():
        if str(entry.get("key_var_name", "")).strip() == key_var:
            return nick
    return None


def _endpoint_provider_nick(config: dict[str, Any], endpoint_name: str) -> str | None:
    from cyt.launch.upstream import infer_upstream_kind_from_url
    from cyt.launch.upstream_credentials import (
        _provider_nick_from_upstream_entry,
        is_canonical_upstream,
        upstream_entry_url,
        upstream_for_endpoint,
    )

    upstream = upstream_for_endpoint(config, endpoint_name)
    if upstream is None:
        return None
    if nick := _provider_nick_from_upstream_entry(config, upstream):
        return nick
    if is_canonical_upstream(upstream):
        return infer_upstream_kind_from_url(upstream_entry_url(upstream))
    return None


def _should_share_client_token_with_pipeline_key(
    config: dict[str, Any],
    *,
    endpoint_name: str,
    pipeline_key_var: str,
) -> bool:
    """True when inbound client auth may override a different pipeline key var."""
    endpoint_provider = _endpoint_provider_nick(config, endpoint_name)
    pipeline_provider = _provider_nick_for_key_var(config, pipeline_key_var)
    if endpoint_provider is None or pipeline_provider is None:
        return False
    return endpoint_provider == pipeline_provider


def _apply_client_token_to_other_pipeline_keys(
    cache: PrunerSettingsCache,
    *,
    config: dict[str, Any],
    client_token: str,
    endpoint_name: str,
    endpoint_key_var: str | None,
) -> PrunerSettingsCache:
    """Use inbound client auth for pruner key vars that share the route provider."""
    updated = cache
    for pipeline_key_var in _pipeline_pruner_key_vars(config):
        if pipeline_key_var == endpoint_key_var:
            continue
        if not _should_share_client_token_with_pipeline_key(
            config,
            endpoint_name=endpoint_name,
            pipeline_key_var=pipeline_key_var,
        ):
            continue
        updated = updated.with_request_upstream_auth(
            client_token,
            config=config,
            upstream_key_var=pipeline_key_var,
        )
    return updated


def apply_request_auth_to_pruner_settings(
    cache: PrunerSettingsCache | None,
    headers: Mapping[str, str],
    config: dict[str, Any] | None,
    endpoint_name: str,
) -> PrunerSettingsCache | None:
    """Prefer inbound client auth and refresh pipeline keys from the process environment."""
    if cache is None or config is None:
        return cache

    updated = cache.with_stage_env_auth(config=config)
    endpoint_key_var = _endpoint_upstream_key_var(config, endpoint_name)
    client_token = extract_request_upstream_token(headers)
    if client_token and endpoint_key_var:
        updated = updated.with_request_upstream_auth(
            client_token,
            config=config,
            upstream_key_var=endpoint_key_var,
        )

    if client_token:
        updated = _apply_client_token_to_other_pipeline_keys(
            updated,
            config=config,
            client_token=client_token,
            endpoint_name=endpoint_name,
            endpoint_key_var=endpoint_key_var,
        )

    return updated


@contextmanager
def request_pruner_settings_scope(
    cache: PrunerSettingsCache | None,
) -> Iterator[PrunerSettingsCache | None]:
    """Install request-scoped pruner settings for nested pruning/skills calls."""
    from cyt.pruners.remote import push_request_pruner_settings, reset_request_pruner_settings

    token = push_request_pruner_settings(cache)
    try:
        yield cache
    finally:
        reset_request_pruner_settings(token)
