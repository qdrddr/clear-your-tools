"""Interactive wizard for ``cyt setup``."""

from __future__ import annotations

import copy
import getpass
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cyt.config import (
    DEFAULT_MCP_TOOL_POLICY,
    DEFAULT_MIN_TOOLS_PRUNING,
    DEFAULT_REVERSE_PORT,
    DEFAULT_STATS_DB_PATH,
    DEFAULT_SYSTEM_TOOL_POLICY,
    POLICY_CHOICES,
    UPSTREAM_URL_DEFAULTS,
    USER_ENV_PATH,
    ToolPolicy,
    deep_merge,
    default_model_nick,
    load_bundled_defaults_yaml,
    load_user_config_overlay,
    merge_model_entry,
    provider_nick_for_dns,
    provider_registry,
    save_user_config,
)

PipelineChoice = Literal["rerank", "llm", "both", "bm25"]
SKILLS_PIPELINE_CHOICES: tuple[str, ...] = ("bm25", "rerank", "llm")
SKILLS_PIPELINE_LABELS: tuple[str, ...] = (
    "bm25 (no API key, local)",
    "rerank (smarter)",
    "llm (more $$, smartest)",
)
SKILLS_PIPELINE_DEFAULT = "bm25"
SKILLS_INJECT_VIA_CHOICES: tuple[str, ...] = ("proxy", "hook")
SKILLS_INJECT_VIA_DEFAULT = "proxy"
DEFAULT_SKILLS_DIRECTORIES: tuple[str, ...] = (
    "~/.claude/skills",
    ".claude/skills",
    "~/.codex/skills",
    ".codex/skills",
)
TOKENS_PER_MILLION = 1_000_000
# Values at or above this (without scientific notation) are treated as USD per 1M tokens.
_USD_PER_MILLION_THRESHOLD = 1e-4
PRIMARY_TOO_CHEAP_USD_PER_MILLION = 0.4
RERANK_PIPELINE_MAX_USD_PER_MILLION = 2.5
PRUNER_MIN_COST_RATIO = 10
PRIMARY_TOO_CHEAP_MESSAGE = (
    "Your primary model is very cheap (< "
    f"${PRIMARY_TOO_CHEAP_USD_PER_MILLION:g}/1M input tokens). "
    "BM25-only pruning is recommended; remote pruners may not save enough to justify their cost."
)
PIPELINE_CHOICE_LABELS: tuple[str, ...] = (
    "rerank only",
    "llm only",
    "rerank and llm (both)",
    "bm25 only",
)
PROVIDER_DOMAIN_DEFAULTS: dict[str, str] = {
    "openrouter": "openrouter.ai",
    "anthropic": "anthropic.com",
    "openai": "openai.com",
    "deepinfra": "deepinfra.com",
}


def upstream_entry_endpoint(entry: dict[str, Any]) -> str:
    """Return reverse-proxy route name from an upstream list entry."""
    value = entry.get("endpoint")
    if value is None or not str(value).strip():
        value = entry.get("upstream")
    if value is None or not str(value).strip():
        return "?"
    return str(value).strip()


def usd_per_million_to_per_token(usd_per_million: float | str) -> float:
    """Convert a price in USD per 1M tokens to per-token cost (scientific form in config)."""
    return float(Decimal(str(usd_per_million)) / Decimal(TOKENS_PER_MILLION))


def per_token_to_usd_per_million(per_token: float) -> float:
    """Convert per-token cost to USD per 1M tokens for display."""
    return per_token * TOKENS_PER_MILLION


def model_input_cost_per_token(entry: dict[str, Any]) -> float | None:
    """Return ``input_cost_per_token`` from a catalog or confirmed model entry."""
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    cost = pricing.get("input_cost_per_token")
    if cost is None:
        return None
    return float(cost)


def model_output_cost_per_token(entry: dict[str, Any]) -> float | None:
    """Return ``output_cost_per_token`` from a catalog or confirmed model entry."""
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    cost = pricing.get("output_cost_per_token")
    if cost is None:
        return None
    return float(cost)


STATS_ADD_COSTS_HINT = "\nWant to see $$$ savings in costs? Run\n\tcyt stats --add-costs"


def model_missing_cost_fields(entry: dict[str, Any]) -> list[str]:
    """Return missing ``input_cost_per_token`` / ``output_cost_per_token`` field names."""
    missing: list[str] = []
    if model_input_cost_per_token(entry) is None:
        missing.append("input_cost_per_token")
    if model_output_cost_per_token(entry) is None:
        missing.append("output_cost_per_token")
    return missing


def model_missing_metadata_fields(
    entry: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Return missing ``provider`` or ``domain_match`` field names for a remote model."""
    if config is not None:
        entry = merge_model_entry(config, entry)
    missing: list[str] = []
    provider = entry.get("provider")
    if not provider or not str(provider).strip():
        missing.append("provider")
    domain_match = entry.get("domain_match")
    if not isinstance(domain_match, list) or not domain_match:
        missing.append("domain_match")
    return missing


def iter_models_missing_costs(
    config: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(kind, entry)`` pairs for remote LLM/reranker models missing token pricing."""
    result: list[tuple[str, dict[str, Any]]] = []
    models = config.get("models", {})
    if not isinstance(models, dict):
        return result
    for kind in ("llm", "rerankers"):
        section = models.get(kind, {})
        if not isinstance(section, dict):
            continue
        remote = section.get("remote", [])
        if not isinstance(remote, list):
            continue
        for entry in remote:
            if isinstance(entry, dict) and model_missing_cost_fields(entry):
                result.append((kind, entry))
    return result


def has_models_missing_costs(config: dict[str, Any]) -> bool:
    """True when any remote LLM or reranker model lacks input/output token pricing."""
    return bool(iter_models_missing_costs(config))


def iter_incomplete_remote_models(
    config: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(kind, entry)`` pairs for remote models missing provider or domain_match."""
    result: list[tuple[str, dict[str, Any]]] = []
    models = config.get("models", {})
    if not isinstance(models, dict):
        return result
    for kind in ("llm", "rerankers"):
        section = models.get(kind, {})
        if not isinstance(section, dict):
            continue
        remote = section.get("remote", [])
        if not isinstance(remote, list):
            continue
        for entry in remote:
            if isinstance(entry, dict) and model_missing_metadata_fields(entry, config=config):
                result.append((kind, entry))
    return result


def input_usd_per_million(entry: dict[str, Any]) -> float | None:
    """Return input price as USD per 1M tokens, or ``None`` if unknown."""
    cost = model_input_cost_per_token(entry)
    if cost is None:
        return None
    return per_token_to_usd_per_million(cost)


def key_var_name_from_provider(provider: str) -> str:
    """Infer env var name from a LiteLLM provider slug (``deepinfra`` → ``DEEPINFRA_API_KEY``)."""
    normalized = provider.strip().upper().replace("-", "_").replace(".", "_")
    if not normalized:
        return ""
    return f"{normalized}_API_KEY"


def print_primary_too_cheap_warning(primary_model: dict[str, Any]) -> None:
    """Warn when the primary model is too cheap for remote pruners to add value."""
    usd = input_usd_per_million(primary_model)
    if usd is not None and usd < PRIMARY_TOO_CHEAP_USD_PER_MILLION:
        print(PRIMARY_TOO_CHEAP_MESSAGE)


def recommended_pipeline_default_index(upstream_llm_model: dict[str, Any]) -> int:
    """Default pruning pipeline index from primary model pricing."""
    usd = input_usd_per_million(upstream_llm_model)
    if usd is None:
        return PIPELINE_CHOICE_LABELS.index("rerank only")
    if usd < PRIMARY_TOO_CHEAP_USD_PER_MILLION:
        return PIPELINE_CHOICE_LABELS.index("bm25 only")
    if usd > RERANK_PIPELINE_MAX_USD_PER_MILLION:
        return PIPELINE_CHOICE_LABELS.index("llm only")
    return PIPELINE_CHOICE_LABELS.index("rerank only")


def max_pruner_input_cost_per_token(primary_model: dict[str, Any]) -> float | None:
    """Maximum pruner input cost so the pruner is at least ``PRUNER_MIN_COST_RATIO``x cheaper."""
    primary_cost = model_input_cost_per_token(primary_model)
    if primary_cost is None:
        return None
    return primary_cost / PRUNER_MIN_COST_RATIO


def filter_catalog_by_max_input_cost(
    catalog: list[dict[str, Any]],
    max_input_cost_per_token: float,
) -> list[dict[str, Any]]:
    """Keep catalog entries at or below *max_input_cost_per_token*."""
    return [
        entry
        for entry in catalog
        if (cost := model_input_cost_per_token(entry)) is not None
        and cost <= max_input_cost_per_token
    ]


def pruner_input_cost_error(
    pruner_model: dict[str, Any],
    max_input_cost_per_token: float,
) -> str | None:
    """Return an error message when the pruner is not cheap enough vs the primary model."""
    cost = model_input_cost_per_token(pruner_model)
    if cost is None:
        return None
    if cost <= max_input_cost_per_token:
        return None
    max_usd = format_cost_prompt_default(max_input_cost_per_token) or "$0"
    return (
        f"The weak pruner must be at least {PRUNER_MIN_COST_RATIO}x cheaper than the "
        f"primary model (max input cost {max_usd} / 1M tokens)."
    )


def parse_cost_per_token(raw: str) -> float:
    """Parse per-token cost from USD per 1M tokens (e.g. ``$5``) or scientific (e.g. ``5e-06``)."""
    text = raw.strip().replace(",", "")
    if not text:
        raise ValueError("empty")
    has_dollar = "$" in text
    text = text.replace("$", "").strip()
    if not text:
        raise ValueError("empty")
    if "e" in text.lower():
        return float(text)
    value = float(text)
    if has_dollar or abs(value) >= _USD_PER_MILLION_THRESHOLD:
        return usd_per_million_to_per_token(text)
    return value


def upstream_hostnames(upstreams: list[dict[str, Any]]) -> list[str]:
    """Unique hostnames extracted from configured upstream URLs."""
    seen: set[str] = set()
    hostnames: list[str] = []
    for upstream in upstreams:
        url = upstream.get("url") or upstream.get("host_url")
        if not url:
            continue
        host = _extract_hostname(str(url))
        if host and host not in seen:
            seen.add(host)
            hostnames.append(host)
    return hostnames


def upstream_hostnames_default(upstreams: list[dict[str, Any]]) -> str:
    """Comma-separated hostnames extracted from configured upstream URLs."""
    return ",".join(upstream_hostnames(upstreams))


def _filter_catalog_by_upstream_domains(
    catalog: list[dict[str, Any]],
    upstream_hosts: set[str],
) -> list[dict[str, Any]]:
    """Keep catalog entries whose domain_match overlaps *upstream_hosts*."""
    if not upstream_hosts:
        return []
    filtered: list[dict[str, Any]] = []
    for entry in catalog:
        domain_match = entry.get("domain_match")
        if not isinstance(domain_match, list) or not domain_match:
            continue
        if any(str(domain) in upstream_hosts for domain in domain_match):
            filtered.append(entry)
    return filtered


def catalog_has_upstream_domain_match(
    catalog: list[dict[str, Any]],
    upstreams: list[dict[str, Any]] | None,
) -> bool:
    """Return whether any catalog entry matches configured upstream hostnames."""
    if not upstreams:
        return False
    upstream_hosts = set(upstream_hostnames(upstreams))
    return bool(_filter_catalog_by_upstream_domains(catalog, upstream_hosts))


def filter_catalog_by_upstreams(
    catalog: list[dict[str, Any]],
    upstreams: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep catalog entries whose domain_match overlaps upstream hostnames.

    Returns the full catalog when upstreams are empty or nothing matches.
    """
    if not upstreams:
        return catalog
    upstream_hosts = set(upstream_hostnames(upstreams))
    if not upstream_hosts:
        return catalog

    filtered = _filter_catalog_by_upstream_domains(catalog, upstream_hosts)
    return filtered if filtered else catalog


def upstream_url_default(upstreams: list[dict[str, Any]]) -> str | None:
    """First upstream URL entered during setup (may include path)."""
    for upstream in upstreams:
        if url := upstream.get("url") or upstream.get("host_url"):
            if text := normalize_upstream_url(str(url)):
                return text
    return None


def upstreams_for_config(upstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize upstream entries for saved config."""
    result: list[dict[str, Any]] = []
    for upstream in upstreams:
        entry: dict[str, Any] = {}
        endpoint = upstream_entry_endpoint(upstream)
        if endpoint != "?":
            entry["endpoint"] = endpoint
        if "kind" in upstream:
            entry["kind"] = normalize_upstream_kind(str(upstream["kind"]))
        if url := upstream.get("url") or upstream.get("host_url"):
            entry["url"] = normalize_upstream_url(str(url))
        result.append(entry)
    return result


def domain_match_default_string(
    provider: str,
    entry: dict[str, Any] | None = None,
    *,
    upstreams: list[dict[str, Any]] | None = None,
    base_url: str | None = None,
) -> str:
    """Default comma-separated hostnames for domain_match prompts."""
    resolved_base_url = base_url
    if not resolved_base_url and entry:
        entry_base_url = entry.get("base_url")
        if isinstance(entry_base_url, str) and entry_base_url.strip():
            resolved_base_url = entry_base_url
    if resolved_base_url:
        if hostname := _extract_hostname(resolved_base_url):
            return hostname
    if upstreams:
        if from_upstreams := upstream_hostnames_default(upstreams):
            return from_upstreams
    if entry:
        domain_match = entry.get("domain_match")
        if isinstance(domain_match, list) and domain_match:
            return ",".join(str(d) for d in domain_match)
    return PROVIDER_DOMAIN_DEFAULTS.get(provider.strip().lower(), "")


def _extract_hostname(part: str) -> str:
    """Return hostname from a URL or plain hostname."""
    text = part.strip()
    if not text:
        return text
    if "://" in text or text.startswith("//"):
        parsed = urlparse(text if "://" in text else f"//{text}")
        if parsed.hostname:
            return parsed.hostname
    if "/" in text:
        return text.split("/", 1)[0]
    return text


def normalize_upstream_url(raw: str) -> str:
    """Return upstream URL, preserving path (e.g. ``/v1``); strip trailing slash only."""
    return raw.strip().rstrip("/")


UPSTREAM_KIND_CHOICES: tuple[str, ...] = ("anthropic", "openai")
UPSTREAM_KIND_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "claude-code": "anthropic",
    "codex": "openai",
}


def _upstream_kind_allowed_display() -> str:
    return ", ".join([*UPSTREAM_KIND_CHOICES, *sorted(UPSTREAM_KIND_ALIASES)])


def normalize_upstream_kind(raw: str) -> str:
    """Return canonical upstream kind (``anthropic`` or ``openai``).

    Accepts aliases ``claude`` / ``claude-code`` (anthropic) and ``codex`` (openai).
    """
    kind = raw.strip().lower()
    kind = UPSTREAM_KIND_ALIASES.get(kind, kind)
    if kind not in UPSTREAM_KIND_CHOICES:
        allowed = _upstream_kind_allowed_display()
        raise ValueError(f"Invalid upstream kind {raw!r}; expected one of: {allowed}")
    return kind


def derive_second_level_domain_from_hostname(hostname: str) -> str:
    """Return the second-level domain label (e.g. ``openrouter`` from ``api.openrouter.ai``)."""
    parts = [part for part in hostname.lower().split(".") if part]
    if len(parts) >= 3:
        return parts[-2]
    if parts:
        return parts[0]
    raise ValueError(f"Cannot derive second-level domain from hostname: {hostname}")


def derive_upstream_name_from_url(raw: str) -> str:
    """Derive upstream endpoint name from the URL hostname's second-level domain."""
    hostname = _extract_hostname(normalize_upstream_url(raw))
    return derive_second_level_domain_from_hostname(hostname)


def build_upstream_cli_overlay(
    upstream_url: str,
    upstream_kind: str,
    *,
    upstream_name: str | None = None,
) -> dict[str, Any]:
    """Build a minimal config overlay from CLI ``--upstream`` / ``--upstream-kind``."""
    kind = normalize_upstream_kind(upstream_kind)
    if upstream_name is not None:
        resolved_name = upstream_name.strip()
        if not resolved_name:
            raise ValueError("upstream name must not be empty")
    else:
        resolved_name = derive_upstream_name_from_url(upstream_url)
    upstream: dict[str, Any] = {
        "endpoint": resolved_name,
        "kind": kind,
        "url": normalize_upstream_url(upstream_url),
    }
    return {
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": upstreams_for_config([upstream]),
                    "endpoints": [resolved_name],
                },
            },
        },
    }


def apply_upstream_cli_to_config(
    config_path: Path,
    *,
    upstream_url: str,
    upstream_kind: str,
    upstream_name: str | None = None,
) -> str:
    """Persist CLI upstream settings into *config_path*; return the endpoint name."""
    config_path = config_path.expanduser()
    existing = load_user_config_overlay(config_path)
    overlay = build_upstream_cli_overlay(
        upstream_url,
        upstream_kind,
        upstream_name=upstream_name,
    )
    new_upstream = overlay["network"]["proxy"]["reverse"]["upstreams"][0]
    new_name = str(new_upstream["endpoint"])
    new_kind = normalize_upstream_kind(str(new_upstream["kind"]))
    new_url = normalize_upstream_url(str(new_upstream["url"]))

    for entry in _reverse_proxy_section(existing).get("upstreams", []):
        if not isinstance(entry, dict):
            continue
        if upstream_entry_endpoint(entry) != new_name:
            continue
        existing_kind = normalize_upstream_kind(str(entry.get("kind", "")))
        existing_url = normalize_upstream_url(
            str(entry.get("url") or entry.get("host_url") or entry.get("base_url") or ""),
        )
        if existing_kind == new_kind and existing_url == new_url:
            endpoints = _reverse_proxy_section(existing).get("endpoints", [])
            if isinstance(endpoints, list) and new_name in [str(item) for item in endpoints]:
                return new_name

    merged = merge_setup_overlay(existing, overlay)
    save_user_config(config_path, merged, apply_bundled_sections=False)
    endpoints = overlay["network"]["proxy"]["reverse"]["endpoints"]
    return str(endpoints[0])


def normalize_base_url(raw: str) -> str:
    """Return full API base URL, preserving path (e.g. ``/v1``); strip trailing slash only."""
    return normalize_upstream_url(raw)


def parse_path_list(raw: str) -> list[str] | None:
    """Parse comma-separated filesystem paths; empty input omits the list."""
    text = raw.strip()
    if not text:
        return None
    paths = [part.strip() for part in text.split(",") if part.strip()]
    return paths or None


def parse_domain_match(raw: str) -> list[str] | None:
    """Parse comma-separated hostnames or API base URLs; empty input omits domain_match."""
    text = raw.strip()
    if not text:
        return None
    domains = [_extract_hostname(part) for part in text.split(",") if part.strip()]
    return domains or None


def _pricing_overlay_from_entry(entry: dict[str, Any]) -> dict[str, float] | None:
    """Return pricing dict from catalog/entry defaults without prompting."""
    result: dict[str, float] = {}
    if (cost := model_input_cost_per_token(entry)) is not None:
        result["input_cost_per_token"] = cost
    if (cost := model_output_cost_per_token(entry)) is not None:
        result["output_cost_per_token"] = cost
    return result or None


def format_cost_prompt_default(per_token: float | None) -> str | None:
    """Format a per-token default as USD per 1M tokens for prompts."""
    if per_token is None:
        return None
    usd = per_token_to_usd_per_million(per_token)
    return f"${usd:g}"


def pipeline_from_choice(choice: PipelineChoice) -> list[str]:
    if choice == "rerank":
        return ["rerank"]
    if choice == "llm":
        return ["llm"]
    if choice == "bm25":
        return ["bm25"]
    return ["rerank", "llm"]


def skills_pipeline_default_from_tool_pipeline(tool_pipeline: list[str]) -> str:
    """Default skills pipeline from the tool pruning pipeline chosen in setup."""
    stages = [str(stage).strip().lower() for stage in tool_pipeline if str(stage).strip()]
    if not stages:
        return SKILLS_PIPELINE_DEFAULT
    if len(stages) == 1 and stages[0] in SKILLS_PIPELINE_CHOICES:
        return stages[0]
    for stage in stages:
        if stage in SKILLS_PIPELINE_CHOICES:
            return stage
    return SKILLS_PIPELINE_DEFAULT


def upsert_remote_model(
    remote_list: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace an existing remote entry with the same nick, or append."""
    nick = entry.get("nick")
    result = [e for e in remote_list if not (nick and e.get("nick") == nick)]
    result.append(entry)
    return result


PROVIDER_REGISTRY_FIELDS = frozenset({"provider", "key_var_name", "domain_match"})


def _provider_nick_from_model(
    entry: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> str:
    nick = entry.get("provider_nick") or entry.get("provider")
    if nick:
        return str(nick).strip()
    domain_match = entry.get("domain_match")
    if isinstance(domain_match, list) and domain_match:
        dns = str(domain_match[0])
        if config is not None:
            inferred = provider_nick_for_dns(config, dns)
            if inferred:
                return inferred
        hostname = _extract_hostname(dns)
        return re.sub(r"[^a-zA-Z0-9]+", "-", hostname).strip("-").lower()
    model_nick = entry.get("nick")
    if isinstance(model_nick, str) and model_nick:
        prefix = model_nick.split("-", 1)[0]
        if config is not None and prefix in provider_registry(config):
            return prefix
    return ""


def provider_entry_from_model(
    entry: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a ``models.providers`` row from inline model fields."""
    provider_nick = _provider_nick_from_model(entry, config=config)
    if not provider_nick:
        return None
    provider = str(entry.get("provider") or provider_nick)
    result: dict[str, Any] = {
        "provider_nick": provider_nick,
        "provider": provider,
    }
    if key_var := entry.get("key_var_name"):
        result["key_var_name"] = str(key_var)
    domain_match = entry.get("domain_match")
    if isinstance(domain_match, list) and domain_match:
        result["domain_match"] = copy.deepcopy(domain_match)
    return result


def canonicalize_model_remote_entry(
    entry: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strip provider-registry fields; keep ``provider_nick`` on model rows."""
    result = copy.deepcopy(entry)
    provider_nick = _provider_nick_from_model(result, config=config)
    if provider_nick:
        result["provider_nick"] = provider_nick
    for field in PROVIDER_REGISTRY_FIELDS:
        result.pop(field, None)
    return result


def merge_provider_entry(
    providers: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    nick = str(entry.get("provider_nick", ""))
    existing = next(
        (
            provider
            for provider in providers
            if isinstance(provider, dict) and str(provider.get("provider_nick", "")) == nick
        ),
        None,
    )
    result = [
        copy.deepcopy(provider)
        for provider in providers
        if isinstance(provider, dict) and str(provider.get("provider_nick", "")) != nick
    ]
    if existing is not None:
        result.append(deep_merge(existing, entry))
    else:
        result.append(copy.deepcopy(entry))
    return result


def providers_from_model_entries(
    entries: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for entry in entries:
        provider = provider_entry_from_model(entry, config=config)
        if provider is not None:
            providers = merge_provider_entry(providers, provider)
    if config is not None:
        registry = provider_registry(config)
        providers = [
            deep_merge(dict(registry.get(str(provider.get("provider_nick", "")), {})), provider)
            for provider in providers
        ]
    return providers


def build_models_config_section(
    llm_remote: list[dict[str, Any]],
    reranker_remote: list[dict[str, Any]] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build canonical ``models`` overlay with providers split from remote entries."""
    reranker_remote = reranker_remote or []
    all_entries = llm_remote + reranker_remote
    providers = providers_from_model_entries(all_entries, config=config)
    models: dict[str, Any] = {
        "providers": providers,
        "llm": {
            "remote": [
                canonicalize_model_remote_entry(entry, config=config) for entry in llm_remote
            ],
        },
    }
    if reranker_remote:
        models["rerankers"] = {
            "remote": [
                canonicalize_model_remote_entry(entry, config=config) for entry in reranker_remote
            ],
        }
    return models


def _ensure_models_providers(
    models: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> None:
    """Move inline provider fields from remote entries into ``models.providers``."""
    if config is None:
        config = {"models": models}
    providers_raw = models.get("providers", [])
    providers = (
        [copy.deepcopy(entry) for entry in providers_raw if isinstance(entry, dict)]
        if isinstance(providers_raw, list)
        else []
    )
    for kind in ("llm", "rerankers"):
        section = models.get(kind, {})
        if not isinstance(section, dict):
            continue
        remote = section.get("remote", [])
        if not isinstance(remote, list):
            continue
        canonical_remote: list[dict[str, Any]] = []
        for entry in remote:
            if not isinstance(entry, dict):
                continue
            provider = provider_entry_from_model(entry, config=config)
            if provider is not None:
                providers = merge_provider_entry(providers, provider)
            canonical_remote.append(canonicalize_model_remote_entry(entry, config=config))
        section["remote"] = canonical_remote
    if providers:
        models["providers"] = providers


def merge_upstream_entry(
    upstream_list: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace an existing upstream with the same endpoint name, or append."""
    name = upstream_entry_endpoint(entry)
    result = [e for e in upstream_list if not (name != "?" and upstream_entry_endpoint(e) == name)]
    result.append(entry)
    return result


def merge_endpoints(
    existing: list[str],
    new_endpoints: list[str],
) -> list[str]:
    """Preserve order: keep *existing*, then append any new endpoint names."""
    result = list(existing)
    for endpoint in new_endpoints:
        if endpoint not in result:
            result.append(endpoint)
    return result


def _merge_remote_models(
    existing: list[Any],
    overlay: list[Any],
) -> list[dict[str, Any]]:
    result = [copy.deepcopy(entry) for entry in existing if isinstance(entry, dict)]
    for entry in overlay:
        if isinstance(entry, dict):
            result = upsert_remote_model(result, copy.deepcopy(entry))
    return result


def _reverse_proxy_section(config: dict[str, Any]) -> dict[str, Any]:
    network = config.get("network")
    if not isinstance(network, dict):
        return {}
    proxy = network.get("proxy")
    if not isinstance(proxy, dict):
        return {}
    reverse = proxy.get("reverse")
    return reverse if isinstance(reverse, dict) else {}


def _merge_reverse_overlay(
    reverse: dict[str, Any],
    existing_reverse: dict[str, Any],
    overlay_reverse: dict[str, Any],
) -> None:
    overlay_upstreams = overlay_reverse.get("upstreams")
    if isinstance(overlay_upstreams, list):
        base_upstreams = existing_reverse.get("upstreams", [])
        if not isinstance(base_upstreams, list):
            base_upstreams = []
        merged_upstreams = [
            copy.deepcopy(entry) for entry in base_upstreams if isinstance(entry, dict)
        ]
        for entry in overlay_upstreams:
            if isinstance(entry, dict):
                merged_upstreams = merge_upstream_entry(
                    merged_upstreams,
                    copy.deepcopy(entry),
                )
        reverse["upstreams"] = merged_upstreams

    overlay_endpoints = overlay_reverse.get("endpoints")
    if isinstance(overlay_endpoints, list):
        base_endpoints = existing_reverse.get("endpoints", [])
        if not isinstance(base_endpoints, list):
            base_endpoints = []
        reverse["endpoints"] = merge_endpoints(
            [str(endpoint) for endpoint in base_endpoints],
            [str(endpoint) for endpoint in overlay_endpoints],
        )


def _merge_models_overlay(
    merged: dict[str, Any],
    existing: dict[str, Any],
    overlay: dict[str, Any],
) -> None:
    existing_models = existing.get("models", {}) if isinstance(existing.get("models"), dict) else {}
    overlay_models = overlay.get("models", {}) if isinstance(overlay.get("models"), dict) else {}
    if not overlay_models:
        return

    models = merged.setdefault("models", {})
    overlay_providers = overlay_models.get("providers")
    if isinstance(overlay_providers, list):
        existing_providers = existing_models.get("providers", [])
        if not isinstance(existing_providers, list):
            existing_providers = []
        providers = [
            copy.deepcopy(entry) for entry in existing_providers if isinstance(entry, dict)
        ]
        for entry in overlay_providers:
            if isinstance(entry, dict):
                providers = merge_provider_entry(providers, copy.deepcopy(entry))
        models["providers"] = providers

    for kind in ("llm", "rerankers"):
        overlay_section = overlay_models.get(kind)
        if not isinstance(overlay_section, dict):
            continue
        overlay_remote = overlay_section.get("remote")
        if not isinstance(overlay_remote, list):
            continue
        existing_section = existing_models.get(kind, {})
        existing_remote = (
            existing_section.get("remote", []) if isinstance(existing_section, dict) else []
        )
        if not isinstance(existing_remote, list):
            existing_remote = []
        section = models.setdefault(kind, {})
        section["remote"] = _merge_remote_models(existing_remote, overlay_remote)

    _ensure_models_providers(models, config=merged)


def merge_setup_overlay(
    existing: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge *overlay* onto *existing*, merging list sections instead of replacing them."""
    merged = deep_merge(existing, overlay)
    reverse = merged.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})
    _merge_reverse_overlay(
        reverse,
        _reverse_proxy_section(existing),
        _reverse_proxy_section(overlay),
    )
    _merge_models_overlay(merged, existing, overlay)
    return merged


def collect_key_var_names(
    models: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Return unique key_var_name values from llm and reranker remote model lists."""
    seen: set[str] = set()
    names: list[str] = []
    for kind in ("llm", "rerankers"):
        section = models.get(kind, {})
        if not isinstance(section, dict):
            continue
        remote = section.get("remote", [])
        if not isinstance(remote, list):
            continue
        for entry in remote:
            if not isinstance(entry, dict):
                continue
            enriched = merge_model_entry(config, entry) if config is not None else entry
            key_var = enriched.get("key_var_name")
            if key_var and key_var not in seen:
                seen.add(str(key_var))
                names.append(str(key_var))
    return names


def format_env_lines(updates: dict[str, str]) -> str:
    """Format KEY=value lines for a .env file."""
    return "\n".join(f"{key}={value}" for key, value in updates.items()) + "\n"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=value lines from an existing .env file."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def write_env_file(
    path: Path,
    updates: dict[str, str],
    *,
    overwrite_existing: bool = False,
) -> None:
    """Merge *updates* into *path* (.env format)."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = parse_env_file(path)
    for key, value in updates.items():
        if key in existing and not overwrite_existing:
            continue
        existing[key] = value
    path.write_text(format_env_lines(existing), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def build_setup_overlay(
    *,
    pipeline: list[str],
    reranker_model: dict[str, Any] | None,
    llm_pruner_model: dict[str, Any] | None,
    minimum_tools: int | None,
    system_tool_policy: ToolPolicy,
    mcp_tool_policy: ToolPolicy,
    reverse_port: int,
    upstreams: list[dict[str, Any]],
    endpoints: list[str],
    stats_db_path: str,
    skills: dict[str, Any] | None = None,
    inject_via: str = SKILLS_INJECT_VIA_DEFAULT,
) -> dict[str, Any]:
    """Build the user config overlay dict from wizard selections."""
    llm_remote: list[dict[str, Any]] = []
    if llm_pruner_model is not None:
        llm_remote = upsert_remote_model(llm_remote, copy.deepcopy(llm_pruner_model))

    reranker_remote: list[dict[str, Any]] = []
    if reranker_model is not None:
        reranker_remote = [copy.deepcopy(reranker_model)]

    models = build_models_config_section(llm_remote, reranker_remote)

    policy: dict[str, Any] = {
        "system_tool": system_tool_policy,
        "mcp_tool": mcp_tool_policy,
    }
    if minimum_tools is not None:
        policy["minimum_tools"] = minimum_tools

    pipelines: dict[str, Any] = {}
    if reranker_model is not None:
        pipelines["rerank"] = {"model_nick": str(reranker_model["nick"])}
    if llm_pruner_model is not None:
        pipelines["llm"] = {"model_nick": str(llm_pruner_model["nick"])}

    pruning: dict[str, Any] = {
        "inject_via": inject_via,
        "tools": {
            "sequence": pipeline,
            "policy": policy,
            "pipelines": pipelines,
        },
    }

    defaults: dict[str, Any] = {}
    if "rerank" in pipeline:
        defaults["reranking_enabled"] = True

    return {
        "defaults": defaults,
        "models": models,
        "pruning": pruning,
        "network": {
            "proxy": {
                "reverse": {
                    "port": reverse_port,
                    "upstreams": upstreams_for_config(upstreams),
                    "endpoints": endpoints,
                },
            },
        },
        "stats": {"database": {"path": stats_db_path}},
        **({"skills": skills} if skills is not None else {}),
    }


def print_proxy_urls(port: int, endpoints: list[str], *, host: str = "localhost") -> None:
    for endpoint in endpoints:
        print(f"http://{host}:{port}/{endpoint}")


def _prompt(text: str, default: str | None = None) -> str:
    if default is not None and default != "":
        suffix = f" [{default}]"
    else:
        suffix = ""
    value = input(f"{text}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def prompt_with_default(text: str, default: str | None = None) -> str:
    """Prompt on stdin with an optional default value."""
    return _prompt(text, default)


def prompt_required(text: str, default: str | None = None) -> str:
    """Prompt until a non-empty value is entered."""
    return _prompt_required(text, default)


def _prompt_required(text: str, default: str | None = None) -> str:
    """Like ``_prompt``, but re-prompt until a non-empty value is entered."""
    while True:
        if value := _prompt(text, default).strip():
            return value
        print("This field is required.", file=sys.stderr)


_KEY_VAR_PROMPT = (
    "Environment variable for API key NAME (not the key itself, e.g. OPENAI_API_KEY). \n"
    "Use key names from https://docs.litellm.ai/docs/providers"
)


def _prompt_key_var_name(*, default: str | None = None, provider: str | None = None) -> str:
    default_key_var = default.strip() if default else None
    if not default_key_var and provider:
        default_key_var = key_var_name_from_provider(provider)
    if default_key_var == "":
        default_key_var = None
    return _prompt_required(_KEY_VAR_PROMPT, default_key_var)


def _prompt_int(text: str, default: int) -> int:
    while True:
        raw = _prompt(text, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Enter a valid integer.", file=sys.stderr)


def _prompt_float(text: str, default: float | None = None) -> float:
    while True:
        default_str = str(default) if default is not None else None
        raw = _prompt(text, default_str)
        try:
            return float(raw)
        except ValueError:
            print("Enter a valid number.", file=sys.stderr)


def _prompt_domain_match(
    provider: str,
    entry: dict[str, Any] | None,
    *,
    upstreams: list[dict[str, Any]] | None = None,
    base_url: str | None = None,
    required: bool = False,
) -> list[str] | None:
    default = domain_match_default_string(
        provider,
        entry,
        upstreams=upstreams,
        base_url=base_url,
    )
    while True:
        raw = _prompt(
            "domain_match (comma-separated hostnames or API base URLs)",
            default,
        )
        parsed = parse_domain_match(raw)
        if parsed is not None:
            return parsed
        if not required:
            return None
        print("domain_match is required (at least one hostname).", file=sys.stderr)


def _prompt_primary_model_input_cost() -> dict[str, Any]:
    """Prompt for the agent's primary/strong model input price (USD per 1M tokens)."""
    print(
        "\n--- Primary/strong model (agent coding model) ---\n"
        "Used to recommend a pruning pipeline and filter weak pruner models "
        f"(must be at least {PRUNER_MIN_COST_RATIO}x cheaper when pricing is known).",
    )
    in_cost = _prompt_cost_per_token(
        "Expected input price for your primary/strong model (the model your agent uses to code)",
        default_per_token=5e-06,
    )
    return {"pricing": {"input_cost_per_token": in_cost}}


def _prompt_cost_per_token(label: str, default_per_token: float | None = None) -> float:
    hint = "USD per 1M tokens (e.g. $5) or per-token (e.g. 5e-06)"
    default_str = format_cost_prompt_default(default_per_token)
    while True:
        raw = _prompt(f"{label} ({hint})", default_str)
        try:
            return parse_cost_per_token(raw)
        except ValueError:
            print("Enter a dollar amount per 1M tokens or a per-token value.", file=sys.stderr)


def _prompt_pruner_input_cost(
    default_per_token: float | None = None,
    *,
    max_input_cost_per_token: float | None = None,
) -> float:
    """Prompt for pruner input cost, enforcing the primary-model cost ratio when set."""
    while True:
        in_cost = _prompt_cost_per_token("input_cost_per_token", default_per_token)
        if max_input_cost_per_token is None:
            return in_cost
        error = pruner_input_cost_error(
            {"pricing": {"input_cost_per_token": in_cost}},
            max_input_cost_per_token,
        )
        if error is None:
            return in_cost
        print(error, file=sys.stderr)
        default_per_token = in_cost


def _prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
    default_label = "Y/n" if default_yes else "y/N"
    raw = _prompt(f"{text} ({default_label})", "y" if default_yes else "n").lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes", "1", "true")


def _prompt_choice[ChoiceT: str](
    text: str,
    choices: list[ChoiceT],
    default_index: int = 0,
) -> ChoiceT:
    for i, choice in enumerate(choices, start=1):
        print(f"  {i}. {choice}")
    while True:
        raw = _prompt(text, str(default_index + 1))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print(f"Choose 1-{len(choices)}.", file=sys.stderr)


def _catalog_merge_config(user_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bundled defaults merged with user config for provider registry lookups."""
    bundled = load_bundled_defaults_yaml()
    if not user_config:
        return bundled
    return deep_merge(bundled, user_config)


def _catalog_entries(kind: str) -> list[dict[str, Any]]:
    bundled = load_bundled_defaults_yaml()
    models = bundled.get("models", {})
    if not isinstance(models, dict):
        return []
    section = models.get(kind, {})
    if not isinstance(section, dict):
        return []
    remote = section.get("remote", [])
    if not isinstance(remote, list):
        return []
    return [copy.deepcopy(e) for e in remote if isinstance(e, dict)]


def _select_model_from_catalog(
    kind: str,
    *,
    label: str,
    prompt_key_var: bool = True,
    domain_match_upstreams: list[dict[str, Any]] | None = None,
    filter_by_upstream_domains: bool = False,
    max_input_cost_per_token: float | None = None,
    custom_default_base_url: str | None = None,
    prompt_custom_base_url: bool = False,
    confirm_fields: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merge_config = _catalog_merge_config(config)
    full_catalog = [merge_model_entry(merge_config, entry) for entry in _catalog_entries(kind)]
    catalog = full_catalog
    default_index = 0
    if filter_by_upstream_domains and domain_match_upstreams:
        if catalog_has_upstream_domain_match(full_catalog, domain_match_upstreams):
            catalog = filter_catalog_by_upstreams(full_catalog, domain_match_upstreams)
        else:
            default_index = len(full_catalog)
    if max_input_cost_per_token is not None:
        catalog = filter_catalog_by_max_input_cost(catalog, max_input_cost_per_token)
    options = [f"{e.get('nick', '?')} ({e.get('provider')}/{e.get('name')})" for e in catalog]
    options.append("Custom…")
    if default_index == len(full_catalog):
        default_index = len(options) - 1
    choice = _prompt_choice(f"Select {label}", options, default_index=default_index)
    if choice != "Custom…":
        idx = options.index(choice)
        entry = copy.deepcopy(catalog[idx])
        if not confirm_fields:
            return entry
        return _confirm_model_fields(
            entry,
            allow_catalog_defaults=True,
            prompt_key_var=prompt_key_var,
            domain_match_upstreams=domain_match_upstreams,
            max_input_cost_per_token=max_input_cost_per_token,
        )

    return _prompt_custom_model(
        prompt_key_var=prompt_key_var,
        domain_match_upstreams=domain_match_upstreams,
        default_base_url=custom_default_base_url,
        prompt_base_url=prompt_custom_base_url,
        max_input_cost_per_token=max_input_cost_per_token,
    )


def _confirm_model_fields(
    entry: dict[str, Any],
    *,
    allow_catalog_defaults: bool = False,
    prompt_key_var: bool = True,
    domain_match_upstreams: list[dict[str, Any]] | None = None,
    max_input_cost_per_token: float | None = None,
) -> dict[str, Any]:
    name = str(entry.get("name", ""))
    provider_default = str(entry.get("provider") or "").strip()
    default_nick = str(entry.get("nick") or default_model_nick(provider_default, name))
    nick = _prompt("Model nick", default_nick)
    name = _prompt("Model name (as seen on the provider's website)", name)
    provider = _prompt_required(
        "Provider (https://docs.litellm.ai/docs/providers)",
        provider_default or None,
    )
    key_var: str | None = None
    if prompt_key_var:
        key_var = _prompt_key_var_name(
            default=str(entry.get("key_var_name", "")),
            provider=provider,
        )
    max_tokens = _prompt_int(
        "max_tokens",
        int(entry.get("max_tokens", 128000)),
    )

    result: dict[str, Any] = {
        "name": name,
        "provider_nick": provider,
        "provider": provider,
        "nick": nick,
        "max_tokens": max_tokens,
    }
    domain_match = entry.get("domain_match")
    if isinstance(domain_match, list) and domain_match:
        result["domain_match"] = copy.deepcopy(domain_match)
    if pricing := _pricing_overlay_from_entry(entry):
        result["pricing"] = pricing
    if key_var is not None:
        result["key_var_name"] = key_var
    if allow_catalog_defaults and entry.get("base_url") is not None:
        result["base_url"] = copy.deepcopy(entry["base_url"])
    return result


def _prompt_custom_model(
    *,
    prompt_key_var: bool = True,
    domain_match_upstreams: list[dict[str, Any]] | None = None,
    default_base_url: str | None = None,
    prompt_base_url: bool = False,
    max_input_cost_per_token: float | None = None,
) -> dict[str, Any]:
    provider = _prompt_required("Provider (https://docs.litellm.ai/docs/providers)")
    name = _prompt("Model name (as seen on the provider's website)")
    nick = _prompt("Model nick", default_model_nick(provider, name))
    while not nick:
        nick = _prompt("Model nick (required)")
    key_var: str | None = None
    if prompt_key_var:
        key_var = _prompt_key_var_name(provider=provider)
    max_tokens = _prompt_int("max_tokens", 128000)
    result: dict[str, Any] = {
        "name": name,
        "provider_nick": provider,
        "provider": provider,
        "nick": nick,
        "max_tokens": max_tokens,
    }
    if key_var is not None:
        result["key_var_name"] = key_var
    if prompt_base_url or default_base_url is not None:
        if base_url := normalize_base_url(
            _prompt(
                "base_url (may leave blank if one of the https://docs.litellm.ai/docs/providers selected)",
                default_base_url or "",
            ),
        ):
            result["base_url"] = base_url
    return result


def _default_minimum_tools(config: dict[str, Any]) -> int:
    """Return configured ``pruning.tools.policy.minimum_tools`` or bundled default."""
    pruning = config.get("pruning")
    if isinstance(pruning, dict):
        tools = pruning.get("tools")
        if isinstance(tools, dict):
            policy = tools.get("policy")
            if isinstance(policy, dict) and policy.get("minimum_tools") is not None:
                return int(policy["minimum_tools"])
    return DEFAULT_MIN_TOOLS_PRUNING


def _pipeline_choice_labels(
    recommended_index: int,
    *,
    minimum_tools: int,
) -> list[str]:
    labels = list(PIPELINE_CHOICE_LABELS)
    bm25_index = labels.index("bm25 only")
    labels[bm25_index] = f"bm25 (no API key, local; Defaults to when below {minimum_tools} tools)"
    return [
        f"{label} (recommended)" if index == recommended_index else label
        for index, label in enumerate(labels)
    ]


def _pipeline_from_display_label(label: str) -> list[str]:
    normalized = label.replace(" (recommended)", "")
    if normalized.startswith("bm25"):
        return pipeline_from_choice("bm25")
    mapping: dict[str, PipelineChoice] = {
        "rerank only": "rerank",
        "llm only": "llm",
        "rerank and llm (both)": "both",
    }
    return pipeline_from_choice(mapping[normalized])


def _prompt_pipeline(
    *,
    recommended_index: int = 0,
    minimum_tools: int = DEFAULT_MIN_TOOLS_PRUNING,
) -> list[str]:
    print("\n--- Tool pruning pipelines ---")
    pipeline_labels = _pipeline_choice_labels(
        recommended_index,
        minimum_tools=minimum_tools,
    )
    choice = _prompt_choice(
        "Select pruning method",
        pipeline_labels,
        default_index=recommended_index,
    )
    return _pipeline_from_display_label(choice)


def _prompt_policy(label: str, default: ToolPolicy) -> ToolPolicy:
    return _prompt_choice(
        label,
        list(POLICY_CHOICES),
        default_index=POLICY_CHOICES.index(default),
    )


def _existing_upstream_setup(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    reverse = _reverse_proxy_section(config)
    upstreams_raw = reverse.get("upstreams", [])
    upstreams = [copy.deepcopy(entry) for entry in upstreams_raw if isinstance(entry, dict)]
    endpoints_raw = reverse.get("endpoints", [])
    endpoints = [str(item) for item in endpoints_raw] if isinstance(endpoints_raw, list) else []
    return upstreams, endpoints


def _upstream_display_fields(upstream: dict[str, Any]) -> tuple[str, str, str]:
    endpoint = upstream_entry_endpoint(upstream)
    kind_raw = upstream.get("kind")
    if kind_raw:
        kind = normalize_upstream_kind(str(kind_raw))
    elif endpoint != "?":
        kind = normalize_upstream_kind(endpoint)
    else:
        kind = "?"
    url = str(upstream.get("url") or upstream.get("host_url") or "?").strip() or "?"
    return endpoint, kind, url


def _format_text_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Return aligned table lines with a header row and underline."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(cells: list[str]) -> str:
        return "  " + "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    lines = [format_row(headers)]
    lines.append("  " + "  ".join("-" * width for width in widths))
    lines.extend(format_row(row) for row in rows)
    return lines


def _print_configured_upstreams(upstreams: list[dict[str, Any]]) -> None:
    """Print configured upstream endpoint, kind, and URL."""
    rows = [_upstream_display_fields(upstream) for upstream in upstreams]
    print("Configured upstreams:")
    for line in _format_text_table(["endpoint", "kind", "url"], [list(row) for row in rows]):
        print(line)
    print()


def _prompt_upstreams(
    existing: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    existing_upstreams: list[dict[str, Any]] = []
    existing_endpoints: list[str] = []
    if existing:
        existing_upstreams, existing_endpoints = _existing_upstream_setup(existing)

    upstreams = list(existing_upstreams)
    endpoints = list(existing_endpoints)

    print("\n--- Upstream API endpoints ---")
    if upstreams:
        _print_configured_upstreams(upstreams)
        if not _prompt_yes_no("Add another upstream?", default_yes=False):
            return upstreams, endpoints
    else:
        print("Configure upstream API endpoints (kind + URL).")
    while True:
        kind = normalize_upstream_kind(
            _prompt(
                "Upstream kind (anthropic/claude/claude-code, openai/codex, or gemini)",
                "anthropic",
            ),
        )
        url = normalize_upstream_url(
            _prompt_required(
                "URL (required)",
                UPSTREAM_URL_DEFAULTS.get(kind, UPSTREAM_URL_DEFAULTS["anthropic"]),
            ),
        )
        upstream: dict[str, Any] = {
            "endpoint": kind,
            "kind": kind,
            "url": url,
        }
        upstreams.append(upstream)
        if kind not in endpoints:
            endpoints.append(kind)
        if not _prompt_yes_no("\nAdd another upstream?", default_yes=False):
            break
    return upstreams, endpoints


def _default_skills_directories(skills_cfg: dict[str, Any]) -> list[str]:
    raw = skills_cfg.get("directories")
    if isinstance(raw, list) and raw:
        return [str(path) for path in raw if str(path).strip()]
    return list(DEFAULT_SKILLS_DIRECTORIES)


def _default_inject_via(existing: dict[str, Any]) -> str:
    from cyt.config import inject_via as resolve_inject_via

    mode = resolve_inject_via(existing)
    return mode if mode in SKILLS_INJECT_VIA_CHOICES else SKILLS_INJECT_VIA_DEFAULT


def _prompt_inject_via(existing: dict[str, Any]) -> str:
    print("\n--- Injection path ---")
    default = _default_inject_via(existing)
    return _prompt_choice(
        "Inject skills and tools via (hook | proxy)",
        list(SKILLS_INJECT_VIA_CHOICES),
        default_index=SKILLS_INJECT_VIA_CHOICES.index(default),
    )


def _prompt_skills_directories(skills_cfg: dict[str, Any]) -> list[str]:
    default_dirs = _default_skills_directories(skills_cfg)
    default_str = ", ".join(default_dirs)
    while True:
        raw = _prompt("Skills directories (comma-separated paths)", default_str)
        parsed = parse_path_list(raw)
        if parsed is not None:
            return parsed
        if default_str:
            return default_dirs
        print("Enter at least one directory path.", file=sys.stderr)


def _prompt_skills(
    existing: dict[str, Any],
    *,
    tool_pipeline: list[str] | None = None,
) -> dict[str, Any]:
    existing_skills = existing.get("skills")
    skills_cfg = existing_skills if isinstance(existing_skills, dict) else {}
    default_enabled = bool(skills_cfg.get("enabled", True))
    print("\n--- Skills injection ---")
    enabled = _prompt_yes_no("Enable skills injection?", default_yes=default_enabled)
    if not enabled:
        return {"enabled": False}
    configured_pipeline = skills_cfg.get("pipeline")
    if isinstance(configured_pipeline, str) and configured_pipeline.strip():
        pipeline = str(configured_pipeline).strip()
    elif tool_pipeline:
        pipeline = skills_pipeline_default_from_tool_pipeline(tool_pipeline)
    else:
        pipeline = SKILLS_PIPELINE_DEFAULT
    try:
        default_index = SKILLS_PIPELINE_CHOICES.index(pipeline)
    except ValueError:
        default_index = SKILLS_PIPELINE_CHOICES.index(SKILLS_PIPELINE_DEFAULT)
    selected_label = _prompt_choice(
        "Skills pruner pipeline",
        list(SKILLS_PIPELINE_LABELS),
        default_index=default_index,
    )
    selected_index = SKILLS_PIPELINE_LABELS.index(selected_label)
    directories = _prompt_skills_directories(skills_cfg)
    return {
        "enabled": True,
        "pipeline": SKILLS_PIPELINE_CHOICES[selected_index],
        "directories": directories,
    }


def _pruning_stage_model_configured(
    config: dict[str, Any],
    stage: Literal["rerank", "llm"],
) -> bool:
    """True when *stage* has a pipeline model nick and matching remote catalog row."""
    from cyt.config import pruning_stage_model_nick

    nick = pruning_stage_model_nick(config, stage, user_config=config)
    if not nick:
        return False
    model_kind = "rerankers" if stage == "rerank" else "llm"
    models = config.get("models")
    if not isinstance(models, dict):
        return False
    section = models.get(model_kind, {})
    if not isinstance(section, dict):
        return False
    remote = section.get("remote", [])
    if not isinstance(remote, list):
        return False
    return any(isinstance(entry, dict) and entry.get("nick") == nick for entry in remote)


def _prompt_skills_pruner_models(
    skills_overlay: dict[str, Any],
    existing: dict[str, Any],
    *,
    reranker_model: dict[str, Any] | None,
    llm_pruner_model: dict[str, Any] | None,
    max_pruner_input_cost: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Prompt for skills rerank/llm models when needed and not already configured."""
    if not skills_overlay.get("enabled"):
        return reranker_model, llm_pruner_model

    skills_pipeline = str(skills_overlay.get("pipeline", ""))
    if skills_pipeline == "rerank" and reranker_model is None:
        if not _pruning_stage_model_configured(existing, "rerank"):
            print("\n--- Reranker (skills injection) model ---")
            reranker_model = _select_model_from_catalog(
                "rerankers",
                label="reranker model",
                prompt_key_var=True,
                max_input_cost_per_token=max_pruner_input_cost,
                prompt_custom_base_url=True,
                config=existing,
            )
    if skills_pipeline == "llm" and llm_pruner_model is None:
        if not _pruning_stage_model_configured(existing, "llm"):
            print("\n--- LLM pruner (skills injection) model ---")
            llm_pruner_model = _select_model_from_catalog(
                "llm",
                label="LLM pruner model",
                prompt_key_var=True,
                max_input_cost_per_token=max_pruner_input_cost,
                prompt_custom_base_url=True,
                config=existing,
            )
    return reranker_model, llm_pruner_model


def _upsert_provider_metadata(
    config: dict[str, Any],
    entry: dict[str, Any],
    updates: dict[str, Any],
) -> None:
    provider_nick = (
        _provider_nick_from_model(entry)
        or str(
            updates.get("provider") or "",
        ).strip()
    )
    if not provider_nick:
        return

    models = config.setdefault("models", {})
    providers_raw = models.get("providers", [])
    providers = (
        [copy.deepcopy(item) for item in providers_raw if isinstance(item, dict)]
        if isinstance(providers_raw, list)
        else []
    )
    provider_entry = next(
        (item for item in providers if str(item.get("provider_nick", "")) == provider_nick),
        None,
    )
    if provider_entry is None:
        provider_entry = {
            "provider_nick": provider_nick,
            "provider": str(updates.get("provider") or provider_nick),
        }
    if "provider" in updates:
        provider_entry["provider"] = updates["provider"]
    if "domain_match" in updates:
        provider_entry["domain_match"] = copy.deepcopy(updates["domain_match"])
    providers = merge_provider_entry(providers, provider_entry)
    models["providers"] = providers

    entry["provider_nick"] = provider_nick
    entry.pop("provider", None)
    entry.pop("domain_match", None)


def _apply_model_metadata_updates(
    config: dict[str, Any],
    entry: dict[str, Any],
    updates: dict[str, Any],
) -> None:
    provider_updates = {key: updates[key] for key in ("provider", "domain_match") if key in updates}
    if provider_updates:
        _upsert_provider_metadata(config, entry, provider_updates)
    pricing_updates = updates.get("pricing")
    if not isinstance(pricing_updates, dict):
        return
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
        entry["pricing"] = pricing
    pricing.update(pricing_updates)


def _prompt_missing_model_metadata(
    entry: dict[str, Any],
    *,
    domain_match_upstreams: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prompt only for provider and domain_match fields missing from *entry*."""
    missing = model_missing_metadata_fields(entry, config=config)
    if not missing:
        return {}

    nick = entry.get("nick", "?")
    name = entry.get("name", "?")
    print(f"\n--- Model: {nick} ({name}) ---")

    updates: dict[str, Any] = {}
    if "provider" in missing:
        updates["provider"] = _prompt_required(
            "Provider (https://docs.litellm.ai/docs/providers)",
        )

    if "domain_match" in missing:
        provider = str(updates.get("provider") or entry.get("provider") or "")
        domain_match = _prompt_domain_match(
            provider,
            entry,
            upstreams=domain_match_upstreams,
            required=True,
        )
        if domain_match is not None:
            updates["domain_match"] = domain_match
    return updates


def _prompt_missing_model_costs(entry: dict[str, Any]) -> dict[str, float]:
    """Prompt only for input/output pricing fields missing from *entry*."""
    missing = model_missing_cost_fields(entry)
    if not missing:
        return {}

    nick = entry.get("nick", "?")
    name = entry.get("name", "?")
    provider = entry.get("provider") or "(unknown provider)"
    print(f"\n--- {nick} ({name}) @ {provider} ---")

    pricing_updates: dict[str, float] = {}
    in_cost = model_input_cost_per_token(entry)
    out_cost = model_output_cost_per_token(entry)
    if "input_cost_per_token" in missing:
        in_cost = _prompt_cost_per_token("input_cost_per_token")
        pricing_updates["input_cost_per_token"] = in_cost
    if "output_cost_per_token" in missing:
        default_out = out_cost if out_cost is not None else in_cost
        out_cost = _prompt_cost_per_token("output_cost_per_token", default_out)
        pricing_updates["output_cost_per_token"] = out_cost
    return pricing_updates


def prompt_incomplete_models_in_config(config: dict[str, Any]) -> bool:
    """Fill missing provider and domain_match on remote models.

    Returns True if anything changed.
    """
    incomplete = iter_incomplete_remote_models(config)
    if not incomplete:
        return False

    reverse = _reverse_proxy_section(config)
    upstreams_raw = reverse.get("upstreams", [])
    upstreams = (
        [u for u in upstreams_raw if isinstance(u, dict)]
        if isinstance(upstreams_raw, list)
        else None
    )

    print("\n--- Model provider & domain_match ---")
    print(
        "Some models in your config are missing provider or domain_match (used by cyt stats).",
    )

    changed = False
    for _kind, entry in incomplete:
        if not model_missing_metadata_fields(entry, config=config):
            continue
        updates = _prompt_missing_model_metadata(
            entry,
            domain_match_upstreams=upstreams,
            config=config,
        )
        if not updates:
            continue
        _apply_model_metadata_updates(config, entry, updates)
        changed = True
    if changed:
        models = config.get("models")
        if isinstance(models, dict):
            _ensure_models_providers(models, config=config)
    return changed


def run_add_costs_wizard(config_path: Path) -> None:
    """Fill missing model metadata/pricing used by ``cyt stats``."""
    config_path = config_path.expanduser()
    config = load_user_config_overlay(config_path)
    metadata_changed = prompt_incomplete_models_in_config(config)

    missing = iter_models_missing_costs(config)
    costs_changed = False
    if missing:
        print("\n--- Model token pricing ---")
        print(
            "Enter input/output costs (USD per 1M tokens) for models missing pricing "
            "(used by cyt stats).",
        )
        for _kind, entry in missing:
            if not model_missing_cost_fields(entry):
                continue
            pricing_updates = _prompt_missing_model_costs(entry)
            if not pricing_updates:
                continue
            _apply_model_metadata_updates(config, entry, {"pricing": pricing_updates})
            costs_changed = True
    elif not metadata_changed:
        print("\nAll LLM and reranker models already have provider, domain_match, and pricing.")

    if metadata_changed or costs_changed:
        models = config.get("models")
        if isinstance(models, dict):
            _ensure_models_providers(models, config=config)
        save_user_config(config_path, config, apply_bundled_sections=False)
        print(f"\nUpdated {config_path}")
    elif missing:
        print("\nNo costs were added.")


def _prompt_env_secrets(
    models: dict[str, Any],
    env_path: Path,
    *,
    config: dict[str, Any] | None = None,
) -> None:
    key_vars = collect_key_var_names(models, config=config)
    if not key_vars:
        return
    env_path = env_path.expanduser()
    existing = parse_env_file(env_path)
    updates: dict[str, str] = {}
    for key_var in key_vars:
        if key_var in existing:
            if not _prompt_yes_no(
                f"{key_var} already in {env_path}. Overwrite?",
                default_yes=False,
            ):
                continue
        if secret := getpass.getpass(f"{key_var}: "):
            updates[key_var] = secret
    if updates:
        write_env_file(
            env_path,
            updates,
            overwrite_existing=True,
        )
        print(f"Wrote {env_path}")
    else:
        print("No new keys written.")


def run_setup(config_path: Path) -> None:
    """Run the interactive setup wizard and write config (and optional .env)."""
    config_path = config_path.expanduser()
    existing = load_user_config_overlay(config_path)
    print(f"CYT proxy setup → {config_path}\n")

    print("--- Proxy Port ---")
    reverse_cfg = _reverse_proxy_section(existing)
    default_port = reverse_cfg.get("port", DEFAULT_REVERSE_PORT)
    reverse_port = _prompt_int("Reverse proxy port", int(default_port))

    upstreams, endpoints = _prompt_upstreams(existing)

    minimum_tools = _prompt_int(
        "\npruning.tools.policy.minimum_tools",
        _default_minimum_tools(existing),
    )

    primary_model = _prompt_primary_model_input_cost()
    print_primary_too_cheap_warning(primary_model)
    recommended_index = recommended_pipeline_default_index(primary_model)

    pipeline = _prompt_pipeline(
        recommended_index=recommended_index,
        minimum_tools=minimum_tools,
    )

    max_pruner_input_cost = max_pruner_input_cost_per_token(primary_model)
    if max_pruner_input_cost is not None:
        max_usd = format_cost_prompt_default(max_pruner_input_cost) or "$0"
        print(
            f"\nPruner models with known input pricing must be at least "
            f"{PRUNER_MIN_COST_RATIO}x cheaper than your primary model "
            f"(max {max_usd} / 1M input tokens).",
        )

    reranker_model: dict[str, Any] | None = None
    llm_pruner_model: dict[str, Any] | None = None

    if "rerank" in pipeline:
        print("\n--- Reranker (weak pruning) model ---")
        reranker_model = _select_model_from_catalog(
            "rerankers",
            label="reranker model",
            prompt_key_var=True,
            max_input_cost_per_token=max_pruner_input_cost,
            prompt_custom_base_url=True,
            config=existing,
        )
    if "llm" in pipeline:
        print("\n--- LLM pruner (weak pruning) model ---")
        llm_pruner_model = _select_model_from_catalog(
            "llm",
            label="LLM pruner model",
            prompt_key_var=True,
            max_input_cost_per_token=max_pruner_input_cost,
            prompt_custom_base_url=True,
            config=existing,
        )

    print("\n--- Tool policies ---")
    system_policy = _prompt_policy(
        "System tools policy",
        DEFAULT_SYSTEM_TOOL_POLICY,
    )
    mcp_policy = _prompt_policy(
        "MCP tools policy",
        DEFAULT_MCP_TOOL_POLICY,
    )

    inject_mode = _prompt_inject_via(existing)

    skills_overlay = _prompt_skills(existing, tool_pipeline=pipeline)
    reranker_model, llm_pruner_model = _prompt_skills_pruner_models(
        skills_overlay,
        existing,
        reranker_model=reranker_model,
        llm_pruner_model=llm_pruner_model,
        max_pruner_input_cost=max_pruner_input_cost,
    )

    from cyt.tools.hook_setup import prompt_tools_hook_config

    tools_overlay = prompt_tools_hook_config(
        existing,
        context="setup",
        inject_mode=inject_mode,
    )

    stats_db = _prompt("Stats database path", DEFAULT_STATS_DB_PATH)

    overlay = build_setup_overlay(
        pipeline=pipeline,
        reranker_model=reranker_model,
        llm_pruner_model=llm_pruner_model,
        minimum_tools=minimum_tools,
        system_tool_policy=system_policy,
        mcp_tool_policy=mcp_policy,
        reverse_port=reverse_port,
        upstreams=upstreams,
        endpoints=endpoints,
        stats_db_path=stats_db,
        skills=skills_overlay,
        inject_via=inject_mode,
    )
    overlay["pruning"]["tools"].update(tools_overlay.get("tools", {}))

    merged = merge_setup_overlay(existing, overlay)
    if save_user_config(config_path, merged, apply_bundled_sections=True):
        print(f"\nWrote {config_path}")

    from cyt.launch.secrets import keyring_backend_available

    if keyring_backend_available():
        print(
            "\nOS keyring is available; skipping .env file setup. "
            "Keys are resolved from the keyring at runtime.",
        )
    elif _prompt_yes_no("\nCreate a .env file for API keys?", default_yes=True):
        env_path = _prompt("Path for .env file", str(USER_ENV_PATH))
        _prompt_env_secrets(merged.get("models", {}), Path(env_path), config=merged)
    else:
        print(
            "Skipping .env. Export keys in your shell instead; "
            "the proxy loads ./.env then ~/.config/cyt/.env at runtime.",
        )

    print("\nProxy endpoint(s):")
    print_proxy_urls(reverse_port, endpoints)
    print("\nNow run:\n")
    print("\tcyt launch\n")
