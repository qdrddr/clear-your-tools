"""Shared remote model resolution for LLM and rerank pruning stages."""

from __future__ import annotations

import sys
from typing import Any, Literal
from urllib.parse import urlparse

from cyt.config import (
    key_var_name_for_model_nick,
    load_config,
    load_user_config_overlay,
    model_responses_api,
    pruning_stage_model_nick,
    remote_model_entry,
    resolve_model,
    stats_provider_for_entry,
)


class RemotePruningSettings:
    """Resolved remote pruning model and credentials from config."""

    __slots__ = (
        "api_key",
        "base_url",
        "model_name",
        "provider",
        "provider_dns",
        "responses_api",
    )

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str | None,
        provider: str | None,
        provider_dns: str | None,
        *,
        responses_api: bool = False,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.provider = provider
        self.provider_dns = provider_dns
        self.responses_api = responses_api


LlmPruningSettings = RemotePruningSettings
RerankPruningSettings = RemotePruningSettings


def resolve_remote_pruning_settings(
    *,
    config: dict[str, Any] | None = None,
    model_kind: str,
    pipeline_name: Literal["rerank", "llm"],
    missing_nick_message: str,
    responses_api: bool = False,
    derive_dns_from_base_url: bool = False,
) -> RemotePruningSettings:
    """Resolve pruning model nick, credentials, and provider metadata from config."""
    if config is None:
        cfg = load_config()
        user = load_user_config_overlay()
    else:
        cfg = config
        user = config

    model_nick = pruning_stage_model_nick(cfg, pipeline_name, user_config=user)
    if not model_nick:
        raise ValueError(missing_nick_message)

    nick = str(model_nick)
    model_name, api_key, base_url = resolve_model(nick, model_kind, "remote", config=cfg)
    key_var = key_var_name_for_model_nick(cfg, model_kind, nick)
    if not api_key:
        from cyt.launch.secrets import _snapshot_env, resolve_credential

        resolved, _source = resolve_credential(
            key_var,
            before_env=_snapshot_env(),
            allow_prompt=False,
        )
        if resolved:
            api_key = resolved
    if not api_key:
        print(f"Error: {key_var} not found.", file=sys.stderr)
        sys.exit(1)

    entry = remote_model_entry(cfg, model_kind, nick)
    provider = stats_provider_for_entry(cfg, entry)
    domain_match = entry.get("domain_match")
    provider_dns = None
    if isinstance(domain_match, list) and domain_match:
        provider_dns = str(domain_match[0])
    elif derive_dns_from_base_url and base_url:
        provider_dns = urlparse(str(base_url)).hostname

    return RemotePruningSettings(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        provider=str(provider) if provider else None,
        provider_dns=provider_dns,
        responses_api=responses_api or model_responses_api(entry),
    )
