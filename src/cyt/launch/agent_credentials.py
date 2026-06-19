"""Agent-facing API credentials for non-canonical reverse-proxy upstreams."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import resolve_config_path
from cyt.launch.config import codex_env_key_name
from cyt.launch.secrets import (
    _snapshot_env,
    resolve_credential,
    resolve_keyring_or_prompt_credential,
    resolve_shell_or_file_credential,
)
from cyt.launch.upstream import AgentName
from cyt.launch.upstream_credentials import (
    ensure_upstream_credential,
    ensure_upstream_provider_registry,
    is_canonical_upstream,
    lookup_upstream_key_var,
    upstream_for_endpoint,
)

_CLAUDE_AGENT_AUTH_VAR = "ANTHROPIC_AUTH_TOKEN"
_OPENAI_UPSTREAM_KEY_VAR = "OPENAI_" + "API_KEY"


@dataclass(frozen=True)
class AgentAuthBinding:
    """Resolved agent auth for a non-canonical upstream launch."""

    agent_env_var: str
    source: str
    token: str
    upstream_key_var: str | None = None


def agent_auth_env_var(agent: AgentName, config: dict[str, Any]) -> str:
    """Return the env var the launched agent reads for API auth."""
    if agent == "claude":
        return _CLAUDE_AGENT_AUTH_VAR
    return codex_env_key_name(config)


def _codex_auth_json_source() -> str:
    from cyt.launch.codex import codex_auth_json_source

    return codex_auth_json_source()


def _agent_source_via_upstream(key_var: str) -> str:
    return f"via {key_var}"


def _binding(
    *,
    agent_env_var: str,
    source: str,
    token: str,
    upstream_key_var: str | None = None,
) -> AgentAuthBinding:
    os.environ[agent_env_var] = token
    return AgentAuthBinding(
        agent_env_var=agent_env_var,
        source=source,
        token=token,
        upstream_key_var=upstream_key_var,
    )


def _agent_source_for_upstream_token(*, upstream_key_var: str) -> str:
    return _agent_source_via_upstream(upstream_key_var)


def _sync_canonical_upstream_credential(
    *,
    token: str,
    agent_source: str,
    agent_env_var: str,
    upstream_key_var: str | None,
    credential_sources: dict[str, str],
) -> None:
    """Mirror a resolved Codex token onto the canonical upstream key for the proxy."""
    if upstream_key_var is None or upstream_key_var == agent_env_var:
        return
    os.environ[upstream_key_var] = token
    if upstream_key_var in credential_sources:
        return
    if agent_source.startswith("via "):
        return
    if agent_source == _codex_auth_json_source():
        credential_sources[upstream_key_var] = agent_source


def _lookup_codex_upstream_key_var(
    *,
    config: dict[str, Any],
    config_path: Path,
    upstream: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Resolve the upstream API-key env var name without loading credentials."""
    if upstream is None:
        return config, None
    if is_canonical_upstream(upstream):
        return config, lookup_upstream_key_var(config, upstream)
    config, _key_var = ensure_upstream_provider_registry(config, config_path, upstream)
    return config, lookup_upstream_key_var(config, upstream)


def _resolve_codex_agent_token(
    *,
    agent_env_var: str,
    upstream_key_var: str | None,
    before_env: dict[str, str],
    allow_prompt: bool,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(token, agent_source, upstream_source)`` for a Codex launch.

    0. Resolve the upstream API-key env var name from a canonical default or an
       explicit upstream ``provider_nick`` (see
       :func:`cyt.launch.upstream_credentials.describe_upstream_key_var_resolution`).
    Then:

    1. Shell export for that env var name
    2. ``./.env`` and ``~/.config/cyt/.env`` for that env var name
    3. ``OPENAI_API_KEY`` from ``~/.codex/auth.json`` when present, non-null, and non-empty
    4. OS keyring for the configured Codex env key
    5. Interactive terminal prompt for the configured Codex env key

    *agent_source* describes how the Codex env var was populated (``via
    OPENAI_API_KEY``, ``keyring``, etc.). *upstream_source* is set when the
    token was copied from an upstream key and records where that key came from
    (shell, env file, ``auth.json``, keyring).
    """
    from cyt.launch.codex import read_codex_auth_openai_api_key

    if upstream_key_var:
        value, source = resolve_shell_or_file_credential(
            upstream_key_var,
            before_env=before_env,
        )
        if value and source:
            return (
                value,
                _agent_source_for_upstream_token(upstream_key_var=upstream_key_var),
                source,
            )

    if auth_value := read_codex_auth_openai_api_key():
        auth_source = _codex_auth_json_source()
        return auth_value, _agent_source_via_upstream(_OPENAI_UPSTREAM_KEY_VAR), auth_source

    value, source = resolve_keyring_or_prompt_credential(
        agent_env_var,
        allow_prompt=False,
    )
    if value and source:
        return value, source, None

    value, source = resolve_keyring_or_prompt_credential(
        agent_env_var,
        allow_prompt=allow_prompt,
    )
    if value and source:
        return value, source, None

    return None, None, None


def ensure_codex_agent_auth(
    *,
    config: dict[str, Any],
    config_path: Path | None,
    endpoint: str,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
    launch_before_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], AgentAuthBinding]:
    """Resolve ``CODEX_OPENAI_API_KEY`` for a Codex launch.

    The upstream API-key env var name is resolved first (``OPENAI_API_KEY`` for
    canonical OpenAI when no ``provider_nick`` is set). Agent credentials are
    then resolved in :func:`_resolve_codex_agent_token` order.
    """
    upstream = upstream_for_endpoint(config, endpoint)
    agent_env_var = codex_env_key_name(config)
    resolved_allow_prompt = sys.stdin.isatty() if allow_prompt is None else allow_prompt
    resolved_before_env = launch_before_env if launch_before_env is not None else _snapshot_env()
    path = resolve_config_path(config_path)

    config, upstream_key_var = _lookup_codex_upstream_key_var(
        config=config,
        config_path=path,
        upstream=upstream,
    )

    if upstream is not None and not is_canonical_upstream(upstream) and upstream_key_var:
        ensure_upstream_credential(
            upstream_key_var,
            credential_sources=credential_sources,
            allow_prompt=resolved_allow_prompt,
            fallback_env_names=(),
        )

    token, agent_source, upstream_source = _resolve_codex_agent_token(
        agent_env_var=agent_env_var,
        upstream_key_var=upstream_key_var,
        before_env=resolved_before_env,
        allow_prompt=resolved_allow_prompt,
    )
    if token and agent_source:
        credential_sources[agent_env_var] = agent_source
        if upstream_source:
            if agent_source == _agent_source_via_upstream(_OPENAI_UPSTREAM_KEY_VAR):
                credential_sources[_OPENAI_UPSTREAM_KEY_VAR] = upstream_source
            elif upstream_key_var is not None:
                credential_sources[upstream_key_var] = upstream_source
        if upstream is not None and is_canonical_upstream(upstream):
            _sync_canonical_upstream_credential(
                token=token,
                agent_source=agent_source,
                agent_env_var=agent_env_var,
                upstream_key_var=upstream_key_var,
                credential_sources=credential_sources,
            )
        return config, _binding(
            agent_env_var=agent_env_var,
            source=agent_source,
            token=token,
            upstream_key_var=upstream_key_var,
        )

    if upstream_key_var:
        raise SystemExit(
            f"Required API key not set for upstream {endpoint!r}.\n"
            f"Export {upstream_key_var} in the shell, add to ~/.config/cyt/.env, "
            f"set OPENAI_API_KEY in {_codex_auth_json_source()}, "
            f"store {agent_env_var} in the keyring, or run interactively.",
        )
    raise SystemExit(
        f"Required Codex API key not set.\n"
        f"Export the upstream API key in the shell, add to ~/.config/cyt/.env, "
        f"set OPENAI_API_KEY in {_codex_auth_json_source()}, "
        f"store {agent_env_var} in the keyring, or run interactively.",
    )


def ensure_agent_upstream_auth(
    *,
    agent: AgentName,
    config: dict[str, Any],
    config_path: Path | None,
    endpoint: str,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
    launch_before_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], AgentAuthBinding | None]:
    """Resolve proxy and agent credentials for a non-canonical upstream endpoint.

    The upstream provider key (e.g. ``OPENROUTER_API_KEY``) is resolved on its
    own (shell env, ``.env``, keyring, prompt) for the reverse proxy and pruning
    pipeline.

    The agent env var (``ANTHROPIC_AUTH_TOKEN`` or ``CODEX_OPENAI_API_KEY``) is
    resolved separately: a direct exported value wins; otherwise it is set from
    the upstream provider key value. Upstream keys are never borrowed from agent
    env vars.
    """
    if agent == "codex":
        return ensure_codex_agent_auth(
            config=config,
            config_path=config_path,
            endpoint=endpoint,
            credential_sources=credential_sources,
            allow_prompt=allow_prompt,
            launch_before_env=launch_before_env,
        )

    upstream = upstream_for_endpoint(config, endpoint)
    if upstream is None or is_canonical_upstream(upstream):
        return config, None

    config, key_var = ensure_upstream_provider_registry(
        config,
        resolve_config_path(config_path),
        upstream,
    )
    agent_env_var = agent_auth_env_var(agent, config)
    resolved_allow_prompt = sys.stdin.isatty() if allow_prompt is None else allow_prompt

    ensure_upstream_credential(
        key_var,
        credential_sources=credential_sources,
        allow_prompt=resolved_allow_prompt,
        fallback_env_names=(),
    )
    upstream_value = os.environ.get(key_var)
    if not upstream_value:
        raise SystemExit(
            f"Required API key not set for upstream {endpoint!r}.\n"
            f"Export {key_var} in the shell, add to ~/.config/cyt/.env, "
            "or run interactively to store it in the keyring.",
        )

    direct_value, direct_source = resolve_credential(
        agent_env_var,
        allow_prompt=False,
    )
    if direct_value and direct_source:
        token = direct_value
        source = direct_source
    else:
        token = upstream_value
        source = _agent_source_via_upstream(key_var)

    credential_sources[agent_env_var] = source
    return config, _binding(
        agent_env_var=agent_env_var,
        source=source,
        token=token,
        upstream_key_var=key_var,
    )
