"""Interactive wizard for ``cyt setup``."""

from __future__ import annotations

import copy
import getpass
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
    save_user_config,
)

PipelineChoice = Literal["rerank", "llm", "both", "bm25"]
TOKENS_PER_MILLION = 1_000_000
# Values at or above this (without scientific notation) are treated as USD per 1M tokens.
_USD_PER_MILLION_THRESHOLD = 1e-4
PRIMARY_TOO_CHEAP_USD_PER_MILLION = 0.4
RERANK_PIPELINE_MAX_USD_PER_MILLION = 2.5
PRUNER_MIN_COST_RATIO = 10
PRIMARY_TOO_CHEAP_MESSAGE = (
    "Do not use this app! As the Primary model is too cheap and this proxy "
    "will not provide any value saving your tokens!"
)
PROVIDER_DOMAIN_DEFAULTS: dict[str, str] = {
    "openrouter": "openrouter.ai",
    "anthropic": "anthropic.com",
    "openai": "openai.com",
    "deepinfra": "deepinfra.com",
}


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


def model_missing_metadata_fields(entry: dict[str, Any]) -> list[str]:
    """Return missing ``provider`` or ``domain_match`` field names for a remote model."""
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
            if isinstance(entry, dict) and model_missing_metadata_fields(entry):
                result.append((kind, entry))
    return result


def input_usd_per_million(entry: dict[str, Any]) -> float | None:
    """Return input price as USD per 1M tokens, or ``None`` if unknown."""
    cost = model_input_cost_per_token(entry)
    if cost is None:
        return None
    return per_token_to_usd_per_million(cost)


def print_primary_too_cheap_warning(upstream_llm_model: dict[str, Any]) -> None:
    """Warn when the primary model is too cheap for this proxy to add value."""
    usd = input_usd_per_million(upstream_llm_model)
    if usd is not None and usd < PRIMARY_TOO_CHEAP_USD_PER_MILLION:
        print(PRIMARY_TOO_CHEAP_MESSAGE)


def recommended_pipeline_default_index(upstream_llm_model: dict[str, Any]) -> int:
    """Default pruning pipeline index: rerank for cheaper primaries, llm for expensive."""
    usd = input_usd_per_million(upstream_llm_model)
    if usd is not None and usd > RERANK_PIPELINE_MAX_USD_PER_MILLION:
        return 1
    return 0


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
        for key in ("upstream", "kind"):
            if key in upstream:
                value = upstream[key]
                if key == "kind":
                    value = normalize_upstream_kind(str(value))
                entry[key] = copy.deepcopy(value)
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
        "upstream": resolved_name,
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
    merged = merge_setup_overlay(existing, overlay)
    save_user_config(config_path, merged, apply_bundled_sections=False)
    endpoints = overlay["network"]["proxy"]["reverse"]["endpoints"]
    return str(endpoints[0])


def normalize_base_url(raw: str) -> str:
    """Return full API base URL, preserving path (e.g. ``/v1``); strip trailing slash only."""
    return normalize_upstream_url(raw)


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


def merge_model_entry(
    remote_list: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace an existing remote entry with the same nick, or append."""
    nick = entry.get("nick")
    result = [e for e in remote_list if not (nick and e.get("nick") == nick)]
    result.append(entry)
    return result


def merge_upstream_entry(
    upstream_list: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace an existing upstream with the same name, or append."""
    name = entry.get("upstream")
    result = [e for e in upstream_list if not (name and e.get("upstream") == name)]
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
            result = merge_model_entry(result, copy.deepcopy(entry))
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


def collect_key_var_names(models: dict[str, Any]) -> list[str]:
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
            key_var = entry.get("key_var_name")
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
    upstream_llm_models: list[dict[str, Any]],
    minimum_tools: int | None,
    system_tool_policy: ToolPolicy,
    mcp_tool_policy: ToolPolicy,
    reverse_port: int,
    upstreams: list[dict[str, Any]],
    endpoints: list[str],
    stats_db_path: str,
) -> dict[str, Any]:
    """Build the user config overlay dict from wizard selections."""
    llm_remote: list[dict[str, Any]] = [copy.deepcopy(model) for model in upstream_llm_models]
    if llm_pruner_model is not None:
        llm_remote = merge_model_entry(llm_remote, copy.deepcopy(llm_pruner_model))

    models: dict[str, Any] = {"llm": {"remote": llm_remote}}

    if reranker_model is not None:
        models["rerankers"] = {"remote": [copy.deepcopy(reranker_model)]}

    policy: dict[str, Any] = {
        "system_tool": system_tool_policy,
        "mcp_tool": mcp_tool_policy,
    }
    if minimum_tools is not None:
        policy["minimum_tools"] = minimum_tools

    pruning: dict[str, Any] = {
        "pipeline": pipeline,
        "policy": policy,
    }
    if reranker_model is not None and "rerank" in pipeline:
        pruning["rerank"] = {
            "model": {
                "remote": {
                    "model_nick": str(reranker_model["nick"]),
                },
            },
        }
    if llm_pruner_model is not None and "llm" in pipeline:
        pruning["llm"] = {
            "model": {
                "remote": {
                    "model_nick": str(llm_pruner_model["nick"]),
                },
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


def _prompt_key_var_name(*, default: str | None = None) -> str:
    default_key_var = default.strip() if default else None
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
) -> dict[str, Any]:
    full_catalog = _catalog_entries(kind)
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
    provider = str(entry.get("provider", ""))
    name = str(entry.get("name", ""))
    default_nick = str(entry.get("nick") or default_model_nick(provider, name))
    nick = _prompt("Model nick", default_nick)
    name = _prompt("Model name (as seen on the provider's website)", name)
    provider = _prompt("Provider (https://docs.litellm.ai/docs/providers)", provider)
    key_var: str | None = None
    if prompt_key_var:
        key_var = _prompt_key_var_name(default=str(entry.get("key_var_name", "")))
    max_tokens = _prompt_int(
        "max_tokens",
        int(entry.get("max_tokens", 128000)),
    )
    domain_match = _prompt_domain_match(
        provider,
        entry,
        upstreams=domain_match_upstreams,
        required=True,
    )

    result: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "nick": nick,
        "max_tokens": max_tokens,
        "domain_match": domain_match,
    }
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
    provider = _prompt("Provider (https://docs.litellm.ai/docs/providers)")
    name = _prompt("Model name (as seen on the provider's website)")
    nick = _prompt("Model nick", default_model_nick(provider, name))
    while not nick:
        nick = _prompt("Model nick (required)")
    key_var: str | None = None
    if prompt_key_var:
        key_var = _prompt_key_var_name()
    max_tokens = _prompt_int("max_tokens", 128000)
    result: dict[str, Any] = {
        "name": name,
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
    domain_match = _prompt_domain_match(
        provider,
        None,
        upstreams=domain_match_upstreams,
        base_url=result.get("base_url"),
        required=True,
    )
    result["domain_match"] = domain_match
    return result


def _pipeline_choice_labels(recommended_index: int) -> list[str]:
    base = ("rerank only", "llm only", "rerank and llm (both)", "bm25 only")
    return [
        f"{label} (recommended)" if index == recommended_index else label
        for index, label in enumerate(base)
    ]


def _prompt_pipeline(*, recommended_index: int = 0) -> list[str]:
    print("\n--- Pruning pipelines ---")
    pipeline_labels = _pipeline_choice_labels(recommended_index)
    choice = _prompt_choice(
        "Select pruning method",
        pipeline_labels,
        default_index=recommended_index,
    )
    mapping: dict[str, PipelineChoice] = {
        "rerank only": "rerank",
        "llm only": "llm",
        "rerank and llm (both)": "both",
        "bm25 only": "bm25",
    }
    normalized = choice.replace(" (recommended)", "")
    return pipeline_from_choice(mapping[normalized])


def _prompt_policy(label: str, default: ToolPolicy) -> ToolPolicy:
    return _prompt_choice(
        label,
        list(POLICY_CHOICES),
        default_index=POLICY_CHOICES.index(default),
    )


def _prompt_primary_upstream_llms_for_upstream(
    upstream: dict[str, Any],
) -> list[dict[str, Any]]:
    """Prompt for at least one primary/strong LLM tied to a single upstream."""
    upstream_only = [upstream]
    upstream_url = upstream_url_default(upstream_only)
    print("\n--- Primary/Strong upstream LLM (for stats / cost tracking) ---")
    models: list[dict[str, Any]] = [
        _select_model_from_catalog(
            "llm",
            label="upstream LLM model",
            prompt_key_var=False,
            domain_match_upstreams=upstream_only,
            filter_by_upstream_domains=True,
            custom_default_base_url=upstream_url,
        ),
    ]
    while _prompt_yes_no(
        "\nAdd another Primary/Strong upstream LLM for this upstream?",
        default_yes=False,
    ):
        models.append(
            _select_model_from_catalog(
                "llm",
                label="upstream LLM model",
                prompt_key_var=False,
                domain_match_upstreams=upstream_only,
                filter_by_upstream_domains=True,
                custom_default_base_url=upstream_url,
            ),
        )
    return models


def _prompt_upstreams() -> tuple[
    list[dict[str, Any]],
    list[str],
    list[dict[str, Any]],
]:
    upstreams: list[dict[str, Any]] = []
    endpoints: list[str] = []
    primary_models: list[dict[str, Any]] = []
    print(
        "Configure upstream API endpoints (kind + URL) and at least one "
        "primary model per upstream.",
    )
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
            "upstream": kind,
            "kind": kind,
            "url": url,
        }
        primary_models.extend(_prompt_primary_upstream_llms_for_upstream(upstream))
        upstreams.append(upstream)
        if kind not in endpoints:
            endpoints.append(kind)
        if not _prompt_yes_no("\nAdd another upstream?", default_yes=False):
            break
    return upstreams, endpoints, primary_models


def _apply_model_metadata_updates(entry: dict[str, Any], updates: dict[str, Any]) -> None:
    if "provider" in updates:
        entry["provider"] = updates["provider"]
    if "domain_match" in updates:
        entry["domain_match"] = updates["domain_match"]
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
) -> dict[str, Any]:
    """Prompt only for provider and domain_match fields missing from *entry*."""
    missing = model_missing_metadata_fields(entry)
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
        if not model_missing_metadata_fields(entry):
            continue
        updates = _prompt_missing_model_metadata(
            entry,
            domain_match_upstreams=upstreams,
        )
        if not updates:
            continue
        _apply_model_metadata_updates(entry, updates)
        changed = True
    return changed


def run_add_costs_wizard(config_path: Path) -> None:
    """Walk through LLM/reranker models missing token pricing and prompt for costs."""
    config_path = config_path.expanduser()
    config = load_user_config_overlay(config_path)
    missing = iter_models_missing_costs(config)
    if not missing:
        print("All LLM and reranker models already have input/output costs configured.")
        return

    print("\n--- Model token pricing ---")
    print(
        "Enter input/output costs (USD per 1M tokens) for models missing pricing "
        "(used by cyt stats).",
    )

    changed = False
    for _kind, entry in missing:
        if not model_missing_cost_fields(entry):
            continue
        pricing_updates = _prompt_missing_model_costs(entry)
        if not pricing_updates:
            continue
        _apply_model_metadata_updates(entry, {"pricing": pricing_updates})
        changed = True

    if changed:
        save_user_config(config_path, config, apply_bundled_sections=False)
        print(f"\nUpdated {config_path}")
    else:
        print("\nNo costs were added.")


def _prompt_env_secrets(models: dict[str, Any], env_path: Path) -> None:
    key_vars = collect_key_var_names(models)
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
    print(f"CYT proxy setup → {config_path.expanduser()}\n")

    print("--- Proxy Port ---")
    reverse_port = _prompt_int("Reverse proxy port", DEFAULT_REVERSE_PORT)
    print()
    print("--- Upstream API endpoints ---")
    upstreams, endpoints, upstream_llm_models = _prompt_upstreams()
    primary_upstream_llm = upstream_llm_models[0]

    for model in upstream_llm_models:
        print_primary_too_cheap_warning(model)

    pipeline = _prompt_pipeline(
        recommended_index=recommended_pipeline_default_index(primary_upstream_llm),
    )

    max_pruner_input_cost = max_pruner_input_cost_per_token(primary_upstream_llm)

    reranker_model: dict[str, Any] | None = None
    llm_pruner_model: dict[str, Any] | None = None
    minimum_tools: int | None = None

    if "rerank" in pipeline:
        print("\n--- Reranker (weak pruning) model ---")
        reranker_model = _select_model_from_catalog(
            "rerankers",
            label="reranker model",
            prompt_key_var=True,
            max_input_cost_per_token=max_pruner_input_cost,
            prompt_custom_base_url=True,
        )
    if "llm" in pipeline:
        print("\n--- LLM pruner (weak pruning) model ---")
        llm_pruner_model = _select_model_from_catalog(
            "llm",
            label="LLM pruner model",
            prompt_key_var=True,
            max_input_cost_per_token=max_pruner_input_cost,
            prompt_custom_base_url=True,
        )
    if "rerank" in pipeline or "llm" in pipeline:
        minimum_tools = _prompt_int(
            "pruning.policy.minimum_tools",
            DEFAULT_MIN_TOOLS_PRUNING,
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

    stats_db = _prompt("Stats database path", DEFAULT_STATS_DB_PATH)

    overlay = build_setup_overlay(
        pipeline=pipeline,
        reranker_model=reranker_model,
        llm_pruner_model=llm_pruner_model,
        upstream_llm_models=upstream_llm_models,
        minimum_tools=minimum_tools,
        system_tool_policy=system_policy,
        mcp_tool_policy=mcp_policy,
        reverse_port=reverse_port,
        upstreams=upstreams,
        endpoints=endpoints,
        stats_db_path=stats_db,
    )

    config_path = config_path.expanduser()
    existing = load_user_config_overlay(config_path)
    merged = merge_setup_overlay(existing, overlay)
    save_user_config(config_path, merged, apply_bundled_sections=True)
    print(f"\nWrote {config_path.expanduser()}")

    if prompt_incomplete_models_in_config(merged):
        save_user_config(config_path, merged, apply_bundled_sections=False)
        print(f"Updated {config_path.expanduser()}")

    if _prompt_yes_no("\nCreate a .env file for API keys?", default_yes=True):
        env_path = _prompt("Path for .env file", str(USER_ENV_PATH))
        _prompt_env_secrets(overlay.get("models", {}), Path(env_path))
    else:
        print(
            "Skipping .env. Export keys in your shell instead; "
            "the proxy loads ./.env then ~/.config/cyt/.env at runtime.",
        )

    print("\nProxy base URL(s):")
    print_proxy_urls(reverse_port, endpoints)
    print("\nNow run proxy with:\n")
    print("\tuv run cyt proxy")
    print("\n\nAnd point Claude Code at the proxy:\n")
    print(f'\texport ANTHROPIC_BASE_URL="http://localhost:{DEFAULT_REVERSE_PORT}/anthropic"')
    print("\tclaude 'say hi' -p")
