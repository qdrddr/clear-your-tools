"""Sync model_request identities from the stats DB into the user config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import DEFAULT_USER_CONFIG_PATH, default_model_nick, save_user_config
from cyt.proxy.setup import (
    _extract_hostname,
    _load_user_config,
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
            nick = entry.get("nick")
            if nick:
                used.add(str(nick))
    return used


def config_has_model_identity(
    remote: list[dict[str, Any]],
    model_name: str,
    provider_dns_name: str | None,
) -> bool:
    """True when *remote* already contains the same name + provider_dns_name pair."""
    for entry in remote:
        if entry.get("name") != model_name:
            continue
        domain_match = entry.get("domain_match")
        if provider_dns_name is None:
            if not isinstance(domain_match, list) or not domain_match:
                return True
            continue
        if isinstance(domain_match, list) and provider_dns_name in {
            str(domain) for domain in domain_match
        }:
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


def build_model_entry(identity: ModelIdentity, nick: str) -> dict[str, Any]:
    """Build a minimal remote model entry from a stats DB identity."""
    entry: dict[str, Any] = {
        "name": identity.model_name,
        "nick": nick,
    }
    if identity.provider:
        entry["provider"] = identity.provider
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
        if config_has_model_identity(
            remote,
            identity.model_name,
            identity.provider_dns_name,
        ):
            continue
        missing.append((kind, identity))
    return missing


def build_models_overlay(
    missing: list[tuple[str, ModelIdentity]],
    *,
    used_nicks: set[str],
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
        entry = build_model_entry(identity, nick)
        if kind == "llm":
            llm_remote.append(entry)
        else:
            rerank_remote.append(entry)

    models: dict[str, Any] = {}
    if llm_remote:
        models["llm"] = {"remote": llm_remote}
    if rerank_remote:
        models["rerankers"] = {"remote": rerank_remote}
    return {"models": models} if models else {}


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
            provider_dns_name=provider_dns_name,
            provider=str(provider) if provider else None,
        )
        for stage, model_name, provider_dns_name, provider in rows
        if model_name
    ]
    if not identities:
        return []

    existing = _load_user_config(config_path)
    missing = identities_missing_from_config(identities, existing)
    if not missing:
        return []

    used_nicks = collect_used_nicks(existing)
    overlay = build_models_overlay(missing, used_nicks=used_nicks)
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
