"""Sync model_request identities from the stats DB into the user config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.common.pricing import normalize_model_name
from cyt.config import (
    DEFAULT_USER_CONFIG_PATH,
    default_model_nick,
    load_user_config_overlay,
    merge_model_entry,
    provider_dns_matches_any,
    provider_nick_for_dns,
    provider_registry,
    save_user_config,
)
from cyt.proxy.setup_wizard import (
    _extract_hostname,
    build_models_config_section,
    derive_second_level_domain_from_hostname,
    merge_setup_overlay,
)
from cyt.proxy.stats import StatsDB

_STAGE_TO_MODEL_KIND: dict[str, str] = {
    "llm": "llm",
    "rerank": "rerankers",
    "upstream": "llm",
}


@dataclass(frozen=True)
class ModelIdentity:
    stage: str
    model_name: str
    provider_dns_name: str | None
    provider: str | None


def _remote_entries(config: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    models = config.get("models", {})
    if not isinstance(models, dict):
        return []
    section = models.get(kind, {})
    if not isinstance(section, dict):
        return []
    remote = section.get("remote", [])
    if not isinstance(remote, list):
        return []
    return [entry for entry in remote if isinstance(entry, dict)]


def collect_used_nicks(config: dict[str, Any]) -> set[str]:
    """Return all nicks declared under models.llm and models.rerankers."""
    used: set[str] = set()
    for kind in ("llm", "rerankers"):
        for entry in _remote_entries(config, kind):
            if nick := entry.get("nick"):
                used.add(str(nick))
    return used


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _model_names_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_text = str(left).strip()
    right_text = str(right).strip()
    if left_text == right_text:
        return True
    return normalize_model_name(left_text) == normalize_model_name(right_text)


def _resolve_identity_provider_nick(
    config: dict[str, Any],
    *,
    provider: str | None,
    provider_dns_name: str | None,
) -> str:
    provider = _empty_to_none(provider)
    provider_dns_name = _empty_to_none(provider_dns_name)
    if provider:
        return provider
    if provider_dns_name:
        inferred = provider_nick_for_dns(config, provider_dns_name)
        if inferred:
            return inferred
        hostname = _extract_hostname(provider_dns_name)
        return re.sub(r"[^a-zA-Z0-9]+", "-", hostname).strip("-").lower()
    return ""


def _entry_provider_nick(config: dict[str, Any], entry: dict[str, Any]) -> str:
    enriched = merge_model_entry(config, entry)
    nick = enriched.get("provider_nick") or enriched.get("provider")
    if nick:
        return str(nick).strip()
    model_nick = entry.get("nick")
    if isinstance(model_nick, str) and model_nick:
        prefix = model_nick.split("-", 1)[0]
        if prefix in provider_registry(config):
            return prefix
    return ""


def _entry_matches_model_identity(
    entry: dict[str, Any],
    *,
    config: dict[str, Any],
    model_name: str,
    provider_dns_name: str | None,
    provider: str | None,
) -> bool:
    if not _model_names_match(entry.get("name"), model_name):
        return False

    provider_dns_name = _empty_to_none(provider_dns_name)
    provider = _empty_to_none(provider)
    identity_provider_nick = _resolve_identity_provider_nick(
        config,
        provider=provider,
        provider_dns_name=provider_dns_name,
    )
    entry_provider_nick = _entry_provider_nick(config, entry)
    enriched = merge_model_entry(config, entry)
    domain_match = enriched.get("domain_match")

    if provider_dns_name:
        if isinstance(domain_match, list) and provider_dns_matches_any(
            provider_dns_name,
            domain_match,
        ):
            return True
        if identity_provider_nick and entry_provider_nick == identity_provider_nick:
            return True
        if identity_provider_nick and isinstance(model_nick := entry.get("nick"), str):
            if model_nick.startswith(f"{identity_provider_nick}-"):
                return True
        return False

    if identity_provider_nick and entry_provider_nick:
        return identity_provider_nick == entry_provider_nick
    if not identity_provider_nick and not entry_provider_nick:
        return True
    return not isinstance(domain_match, list) or not domain_match


def config_has_model_identity(
    remote: list[dict[str, Any]],
    model_name: str,
    provider_dns_name: str | None,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    """True when *remote* already contains the same name + provider identity."""
    if config is None:
        config = {}
    for entry in remote:
        if _entry_matches_model_identity(
            entry,
            config=config,
            model_name=model_name,
            provider_dns_name=provider_dns_name,
            provider=None,
        ):
            return True
    return False


def config_has_model_identity_record(
    remote: list[dict[str, Any]],
    identity: ModelIdentity,
    *,
    config: dict[str, Any],
) -> bool:
    """True when *remote* already contains *identity*."""
    for entry in remote:
        if _entry_matches_model_identity(
            entry,
            config=config,
            model_name=identity.model_name,
            provider_dns_name=identity.provider_dns_name,
            provider=identity.provider,
        ):
            return True
    return False


def _base_nick(
    model_name: str,
    *,
    provider: str | None,
    provider_dns_name: str | None,
) -> str:
    if provider:
        return default_model_nick(provider, model_name)
    short = model_name.rsplit("/", 1)[-1]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", short).strip("-").lower()
    if provider_dns_name:
        hostname = _extract_hostname(provider_dns_name)
        try:
            host_prefix = derive_second_level_domain_from_hostname(hostname)
        except ValueError:
            host_prefix = hostname.split(".", 1)[0]
        slug = f"{host_prefix}-{slug}" if slug else host_prefix
    return slug or "model"


def make_unique_nick(
    model_name: str,
    *,
    provider: str | None,
    provider_dns_name: str | None,
    used: set[str],
) -> str:
    """Allocate a nick unique across all configured remote models."""
    base = _base_nick(
        model_name,
        provider=provider,
        provider_dns_name=provider_dns_name,
    )
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def build_model_entry(
    identity: ModelIdentity,
    nick: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal remote model entry from a stats DB identity."""
    entry: dict[str, Any] = {
        "name": identity.model_name,
        "nick": nick,
    }
    provider_nick = identity.provider
    if not provider_nick and config is not None:
        provider_nick = provider_nick_for_dns(config, identity.provider_dns_name)
    if not provider_nick and identity.provider_dns_name:
        hostname = _extract_hostname(identity.provider_dns_name)
        provider_nick = re.sub(r"[^a-zA-Z0-9]+", "-", hostname).strip("-").lower()
    if provider_nick:
        entry["provider_nick"] = provider_nick
        entry["provider"] = provider_nick
    if identity.provider_dns_name:
        entry["domain_match"] = [identity.provider_dns_name]
    return entry


def identities_missing_from_config(
    identities: list[ModelIdentity],
    config: dict[str, Any],
) -> list[tuple[str, ModelIdentity]]:
    """Return (model_kind, identity) pairs that are not yet in the user config."""
    missing: list[tuple[str, ModelIdentity]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for identity in identities:
        kind = _STAGE_TO_MODEL_KIND.get(identity.stage)
        if kind is None:
            continue
        key = (kind, identity.model_name, identity.provider_dns_name)
        if key in seen:
            continue
        seen.add(key)
        remote = _remote_entries(config, kind)
        if config_has_model_identity_record(remote, identity, config=config):
            continue
        missing.append((kind, identity))
    return missing


def build_models_overlay(
    missing: list[tuple[str, ModelIdentity]],
    *,
    used_nicks: set[str],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a models overlay containing only entries to append."""
    llm_remote: list[dict[str, Any]] = []
    rerank_remote: list[dict[str, Any]] = []
    for kind, identity in missing:
        nick = make_unique_nick(
            identity.model_name,
            provider=identity.provider,
            provider_dns_name=identity.provider_dns_name,
            used=used_nicks,
        )
        entry = build_model_entry(identity, nick, config=config)
        if kind == "llm":
            llm_remote.append(entry)
        else:
            rerank_remote.append(entry)

    if not llm_remote and not rerank_remote:
        return {}
    models = build_models_config_section(llm_remote, rerank_remote, config=config)
    return {"models": models}


def sync_models_from_stats_db(
    db_path: str,
    user_config_path: Path | None = None,
) -> list[str]:
    """Append models seen in stats but missing from the user config file.

    Returns human-readable lines describing changes (empty when nothing was added).
    """
    config_path = (user_config_path or DEFAULT_USER_CONFIG_PATH).expanduser()
    db = StatsDB.open_for_query(db_path)
    if db is None:
        return []
    try:
        rows = db.query_distinct_model_identities()
    finally:
        db.close()

    identities = [
        ModelIdentity(
            stage=str(stage),
            model_name=str(model_name),
            provider_dns_name=_empty_to_none(provider_dns_name),
            provider=_empty_to_none(str(provider) if provider else None),
        )
        for stage, model_name, provider_dns_name, provider in rows
        if model_name
    ]
    if not identities:
        return []

    existing = load_user_config_overlay(config_path)
    missing = identities_missing_from_config(identities, existing)
    if not missing:
        return []

    used_nicks = collect_used_nicks(existing)
    overlay = build_models_overlay(missing, used_nicks=used_nicks, config=existing)
    merged = merge_setup_overlay(existing, overlay)
    save_user_config(config_path, merged, apply_bundled_sections=False)

    lines: list[str] = []
    for kind, identity in missing:
        dns = identity.provider_dns_name or "(no dns)"
        lines.append(
            f"sync: added {kind} model {identity.model_name!r} @ {dns} "
            f"(stage={identity.stage}) -> {config_path}",
        )
    return lines
