"""Extract inline model provider fields into models.providers registry."""

from __future__ import annotations

import copy
from typing import Any

from cyt.migrations.base import ConfigScope, deep_copy_config, ensure_dict, set_schema_stamp
from cyt.migrations.providers import normalize_provider_entry, provider_registry_index

revision = "003_model_provider_registry"
down_revision = "002_pruning_tools_namespace"
applies_to = "both"

_PROVIDER_FIELDS = ("provider", "key_var_name", "base_url", "domain_match")
_MODEL_KINDS = ("llm", "rerankers")


def _default_provider_nick(entry: dict[str, Any]) -> str | None:
    nick = entry.get("provider_nick")
    if nick:
        return str(nick)
    provider = entry.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    return None


def _upsert_provider(
    registry: dict[str, dict[str, Any]],
    provider_nick: str,
    fields: dict[str, Any],
) -> None:
    existing = registry.get(provider_nick, {})
    merged = {**existing, **fields, "provider_nick": provider_nick}
    if "provider" not in merged:
        merged["provider"] = provider_nick
    registry[provider_nick] = merged


def _migrate_remote_entries(
    cfg: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    model_kind: str,
) -> None:
    models = cfg.get("models")
    if not isinstance(models, dict):
        return
    kind_block = models.get(model_kind)
    if not isinstance(kind_block, dict):
        return
    remote = kind_block.get("remote")
    if not isinstance(remote, list):
        return
    for entry in remote:
        if not isinstance(entry, dict):
            continue
        provider_nick = _default_provider_nick(entry)
        if not provider_nick:
            continue
        inline_fields = {key: copy.deepcopy(entry[key]) for key in _PROVIDER_FIELDS if key in entry}
        if inline_fields:
            _upsert_provider(registry, provider_nick, inline_fields)
            for key in _PROVIDER_FIELDS:
                entry.pop(key, None)
        if "provider_nick" not in entry:
            entry["provider_nick"] = provider_nick


def _normalize_providers_list(cfg: dict[str, Any], registry: dict[str, dict[str, Any]]) -> None:
    models = cfg.get("models")
    if not isinstance(models, dict):
        return
    providers = models.get("providers")
    if isinstance(providers, list):
        for item in providers:
            if not isinstance(item, dict):
                continue
            normalized = normalize_provider_entry(item)
            if normalized is None:
                continue
            nick = normalized.get("provider_nick")
            if nick:
                _upsert_provider(registry, str(nick), normalized)
    elif isinstance(providers, dict):
        for nick, item in providers.items():
            if isinstance(item, dict):
                _upsert_provider(registry, str(nick), {**item, "provider_nick": str(nick)})


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    models = result.get("models")
    providers_raw = models.get("providers") if isinstance(models, dict) else None
    providers_list = providers_raw if isinstance(providers_raw, list) else []
    registry = provider_registry_index(providers_list)
    _normalize_providers_list(result, registry)
    for model_kind in _MODEL_KINDS:
        _migrate_remote_entries(result, registry, model_kind)
    if registry:
        models = ensure_dict(result, "models")
        models["providers"] = [registry[nick] for nick in sorted(registry)]
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 003_model_provider_registry")
