"""Request-scoped upstream auth for pruning pipelines inside the reverse proxy."""

from __future__ import annotations

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


def apply_request_auth_to_pruner_settings(
    cache: PrunerSettingsCache | None,
    headers: Mapping[str, str],
    config: dict[str, Any] | None,
    endpoint_name: str,
) -> PrunerSettingsCache | None:
    """Prefer the client's upstream auth token for matching pruning pipeline keys."""
    if cache is None or config is None:
        return cache
    token = extract_request_upstream_token(headers)
    if not token:
        return cache

    from cyt.launch.upstream_credentials import lookup_upstream_key_var, upstream_for_endpoint

    upstream = upstream_for_endpoint(config, endpoint_name)
    if upstream is None:
        return cache
    upstream_key_var = lookup_upstream_key_var(config, upstream)
    if not upstream_key_var:
        return cache
    return cache.with_request_upstream_auth(
        token,
        config=config,
        upstream_key_var=upstream_key_var,
    )


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
