"""Agent-facing API credentials for non-canonical reverse-proxy upstreams."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import resolve_config_path
from cyt.launch.config import codex_env_key_name
from cyt.launch.secrets import resolve_credential, resolve_shell_or_file_credential
from cyt.launch.upstream import AgentName
from cyt.launch.upstream_credentials import (
    ensure_upstream_credential,
    ensure_upstream_provider_registry,
    is_canonical_upstream,
    lookup_upstream_key_var,
    upstream_for_endpoint,
)

_CLAUDE_AGENT_AUTH_VAR = "ANTHROPIC_AUTH_TOKEN"
_CODEX_AUTH_JSON_SOURCE = "~/.codex/auth.json"


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


def _agent_source_via_upstream(key_var: str, upstream_source: str) -> str:
    if upstream_source:
        return f"{upstream_source} (via {key_var})"
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


def _ensure_upstream_provider_key(
    *,
    config: dict[str, Any],
    config_path: Path,
    upstream: dict[str, Any],
    credential_sources: dict[str, str],
    allow_prompt: bool,
) -> tuple[dict[str, Any], str]:
    if is_canonical_upstream(upstream):
        key_var = lookup_upstream_key_var(config, upstream)
        if key_var is None:
            raise SystemExit("Cannot resolve API key env var for canonical upstream.")
        ensure_upstream_credential(
            key_var,
            credential_sources=credential_sources,
            allow_prompt=allow_prompt,
            fallback_env_names=(),
        )
        return config, key_var

    config, key_var = ensure_upstream_provider_registry(config, config_path, upstream)
    ensure_upstream_credential(
        key_var,
        credential_sources=credential_sources,
        allow_prompt=allow_prompt,
        fallback_env_names=(),
    )
    return config, key_var


def ensure_codex_agent_auth(
    *,
    config: dict[str, Any],
    config_path: Path | None,
    endpoint: str,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
) -> tuple[dict[str, Any], AgentAuthBinding]:
    """Resolve ``CODEX_OPENAI_API_KEY`` for a Codex launch.

    Resolution order:
    1. Shell export or ``.env`` value for the configured Codex env key
    2. ``OPENAI_API_KEY`` from ``~/.codex/auth.json`` when non-null
    3. Upstream ``provider_nick`` registry key (shell, ``.env``, keyring, prompt)
    4. Codex env key from keyring, then interactive prompt
    """
    from cyt.launch.codex import read_codex_auth_openai_api_key

    upstream = upstream_for_endpoint(config, endpoint)
    agent_env_var = codex_env_key_name(config)
    resolved_allow_prompt = sys.stdin.isatty() if allow_prompt is None else allow_prompt
    path = resolve_config_path(config_path)

    upstream_key_var: str | None = None
    if upstream is not None and not is_canonical_upstream(upstream):
        config, upstream_key_var = _ensure_upstream_provider_key(
            config=config,
            config_path=path,
            upstream=upstream,
            credential_sources=credential_sources,
            allow_prompt=resolved_allow_prompt,
        )

    direct_value, direct_source = resolve_shell_or_file_credential(agent_env_var)
    if direct_value and direct_source:
        credential_sources[agent_env_var] = direct_source
        return config, _binding(
            agent_env_var=agent_env_var,
            source=direct_source,
            token=direct_value,
            upstream_key_var=upstream_key_var,
        )

    if auth_value := read_codex_auth_openai_api_key():
        credential_sources[agent_env_var] = _CODEX_AUTH_JSON_SOURCE
        return config, _binding(
            agent_env_var=agent_env_var,
            source=_CODEX_AUTH_JSON_SOURCE,
            token=auth_value,
            upstream_key_var=upstream_key_var,
        )

    key_var: str | None = upstream_key_var
    if upstream is not None and key_var is None:
        config, key_var = _ensure_upstream_provider_key(
            config=config,
            config_path=path,
            upstream=upstream,
            credential_sources=credential_sources,
            allow_prompt=resolved_allow_prompt,
        )

    if key_var:
        provider_value = os.environ.get(key_var)
        if provider_value:
            upstream_source = credential_sources.get(key_var, "resolved")
            source = _agent_source_via_upstream(key_var, upstream_source)
            credential_sources[agent_env_var] = source
            return config, _binding(
                agent_env_var=agent_env_var,
                source=source,
                token=provider_value,
                upstream_key_var=key_var,
            )

    fallback_value, fallback_source = resolve_credential(
        agent_env_var,
        allow_prompt=resolved_allow_prompt,
    )
    if fallback_value and fallback_source:
        credential_sources[agent_env_var] = fallback_source
        return config, _binding(
            agent_env_var=agent_env_var,
            source=fallback_source,
            token=fallback_value,
            upstream_key_var=key_var,
        )

    if key_var:
        raise SystemExit(
            f"Required API key not set for upstream {endpoint!r}.\n"
            f"Export {key_var} in the shell, add to ~/.config/cyt/.env, "
            "store it in the keyring, or run interactively.",
        )
    raise SystemExit(
        f"Required Codex API key not set.\n"
        f"Export {agent_env_var} in the shell, add to ~/.config/cyt/.env, "
        f"set OPENAI_API_KEY in {_CODEX_AUTH_JSON_SOURCE}, "
        "or run interactively.",
    )


def ensure_agent_upstream_auth(
    *,
    agent: AgentName,
    config: dict[str, Any],
    config_path: Path | None,
    endpoint: str,
    credential_sources: dict[str, str],
    allow_prompt: bool | None = None,
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
        upstream_source = credential_sources.get(key_var, "resolved")
        source = _agent_source_via_upstream(key_var, upstream_source)

    credential_sources[agent_env_var] = source
    return config, _binding(
        agent_env_var=agent_env_var,
        source=source,
        token=token,
        upstream_key_var=key_var,
    )
