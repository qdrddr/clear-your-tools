"""Provider registry normalization for config migrations and loader."""

from __future__ import annotations

from typing import Any


def normalize_provider_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    if "provider_nick" in item:
        nick = str(item["provider_nick"])
        provider = item.get("provider") or item.get("name") or nick
        return {**item, "provider": provider}
    if len(item) == 1:
        key, nested = next(iter(item.items()))
        if isinstance(nested, dict):
            nick = str(nested.get("provider_nick", key))
            provider = nested.get("provider") or nested.get("name") or key
            return {**nested, "provider_nick": nick, "provider": provider}
    return None


def provider_registry_index(providers: object) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    if not isinstance(providers, list):
        return registry
    for item in providers:
        if not isinstance(item, dict):
            continue
        normalized = normalize_provider_entry(item)
        if normalized is None:
            continue
        nick = normalized.get("provider_nick")
        if nick:
            registry[str(nick)] = normalized
    return registry
