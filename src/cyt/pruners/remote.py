"""Shared remote model resolution for LLM and rerank pruning stages."""

from __future__ import annotations

import os
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
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

_request_pruner_settings: ContextVar[PrunerSettingsCache | None] = ContextVar(
    "request_pruner_settings",
    default=None,
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

    def with_api_key(self, api_key: str) -> RemotePruningSettings:
        return RemotePruningSettings(
            self.model_name,
            api_key,
            self.base_url,
            self.provider,
            self.provider_dns,
            responses_api=self.responses_api,
        )


LlmPruningSettings = RemotePruningSettings
RerankPruningSettings = RemotePruningSettings


@dataclass
class PrunerSettingsCache:
    """Startup-resolved remote pruner settings keyed by pipeline stage."""

    llm: RemotePruningSettings | None = None
    rerank: RemotePruningSettings | None = None

    def for_stage(self, stage: Literal["llm", "rerank"]) -> RemotePruningSettings | None:
        return self.llm if stage == "llm" else self.rerank

    def with_request_upstream_auth(
        self,
        token: str,
        *,
        config: dict[str, Any],
        upstream_key_var: str,
    ) -> PrunerSettingsCache:
        """Clone cache entries whose configured key var matches the upstream provider."""
        return PrunerSettingsCache(
            llm=_override_stage_auth(
                self.llm,
                stage="llm",
                token=token,
                config=config,
                upstream_key_var=upstream_key_var,
            ),
            rerank=_override_stage_auth(
                self.rerank,
                stage="rerank",
                token=token,
                config=config,
                upstream_key_var=upstream_key_var,
            ),
        )

    def with_stage_env_auth(self, *, config: dict[str, Any]) -> PrunerSettingsCache:
        """Refresh each stage from its configured key var in the process environment."""
        llm = _apply_stage_env_auth(self.llm, stage="llm", config=config)
        rerank = _apply_stage_env_auth(self.rerank, stage="rerank", config=config)
        if llm is self.llm and rerank is self.rerank:
            return self
        return PrunerSettingsCache(llm=llm, rerank=rerank)


def push_request_pruner_settings(
    cache: PrunerSettingsCache | None,
) -> Token[PrunerSettingsCache | None]:
    return _request_pruner_settings.set(cache)


def reset_request_pruner_settings(token: Token[PrunerSettingsCache | None]) -> None:
    _request_pruner_settings.reset(token)


def request_pruner_settings() -> PrunerSettingsCache | None:
    return _request_pruner_settings.get()


def _stage_model_kind(stage: Literal["llm", "rerank"]) -> str:
    return "llm" if stage == "llm" else "rerankers"


def _stage_key_var_name(
    config: dict[str, Any],
    stage: Literal["llm", "rerank"],
) -> str | None:
    model_nick = pruning_stage_model_nick(config, stage)
    if not model_nick:
        return None
    try:
        return key_var_name_for_model_nick(config, _stage_model_kind(stage), str(model_nick))
    except ValueError:
        return None


def _override_stage_auth(
    settings: RemotePruningSettings | None,
    *,
    stage: Literal["llm", "rerank"],
    token: str,
    config: dict[str, Any],
    upstream_key_var: str,
) -> RemotePruningSettings | None:
    if settings is None:
        return None
    stage_key_var = _stage_key_var_name(config, stage)
    if stage_key_var != upstream_key_var:
        return settings
    return settings.with_api_key(token)


def _apply_stage_env_auth(
    settings: RemotePruningSettings | None,
    *,
    stage: Literal["llm", "rerank"],
    config: dict[str, Any],
) -> RemotePruningSettings | None:
    if settings is None:
        return None
    stage_key_var = _stage_key_var_name(config, stage)
    if not stage_key_var:
        return settings
    token = os.environ.get(stage_key_var, "").strip()
    if not token or token == settings.api_key:
        return settings
    return settings.with_api_key(token)


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
        print(
            f"Error: {key_var} is not set in the process environment.\n"
            "The proxy resolves pruning pipeline API keys at startup "
            "(shell env, .env, keyring). Restart the proxy after exporting the key "
            "or run `cyt setup` to store one.",
            file=sys.stderr,
        )
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
