"""Interactive wizard for ``cyt-rproxy setup``."""

from __future__ import annotations

import copy
import getpass
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from cyt.config import (
    DEFAULT_MCP_TOOL_POLICY,
    DEFAULT_STATS_DB_PATH,
    DEFAULT_SYSTEM_TOOL_POLICY,
    USER_ENV_PATH,
    default_model_nick,
    load_bundled_defaults_yaml,
    save_user_config,
)

PipelineChoice = Literal["rerank", "llm", "both"]
ToolPolicy = Literal["always_include", "prune_optional", "prune_all"]

DEFAULT_LLM_MINIMUM_TOOLS = 50
DEFAULT_RERANKER_MINIMUM_TOOLS = 29
DEFAULT_REVERSE_PORT = 8834
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
POLICY_CHOICES: tuple[ToolPolicy, ...] = (
    "always_include",
    "prune_optional",
    "prune_all",
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
    """Unique hostnames extracted from configured upstream host URLs."""
    seen: set[str] = set()
    hostnames: list[str] = []
    for upstream in upstreams:
        host_url = upstream.get("host_url")
        if not host_url:
            continue
        host = _extract_hostname(str(host_url))
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


def upstream_host_url_default(upstreams: list[dict[str, Any]]) -> str | None:
    """First upstream API host URL (with path) entered during setup."""
    for upstream in upstreams:
        host_url = upstream.get("host_url")
        if host_url:
            text = normalize_base_url(str(host_url))
            if text:
                return text
    return None


def upstreams_for_config(upstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize upstream entries for saved config (``host_url`` with path preserved)."""
    result: list[dict[str, Any]] = []
    for upstream in upstreams:
        entry: dict[str, Any] = {}
        for key in ("upstream", "host_url", "kind"):
            if key in upstream:
                entry[key] = copy.deepcopy(upstream[key])
        result.append(entry)
    return result


def domain_match_default_string(
    provider: str,
    entry: dict[str, Any] | None = None,
    *,
    upstreams: list[dict[str, Any]] | None = None,
) -> str:
    """Default comma-separated hostnames for domain_match prompts."""
    if upstreams:
        from_upstreams = upstream_hostnames_default(upstreams)
        if from_upstreams:
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


def normalize_base_url(raw: str) -> str:
    """Return full API base URL, preserving path (e.g. ``/v1``); strip trailing slash only."""
    return raw.strip().rstrip("/")


def parse_domain_match(raw: str) -> list[str] | None:
    """Parse comma-separated hostnames or API base URLs; empty input omits domain_match."""
    text = raw.strip()
    if not text:
        return None
    domains = [_extract_hostname(part) for part in text.split(",") if part.strip()]
    return domains or None


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
    upstream_llm_model: dict[str, Any],
    llm_minimum_tools: int | None,
    reranker_minimum_tools: int | None,
    system_tool_policy: ToolPolicy,
    mcp_tool_policy: ToolPolicy,
    reverse_port: int,
    upstreams: list[dict[str, Any]],
    endpoints: list[str],
    stats_db_path: str,
) -> dict[str, Any]:
    """Build the user config overlay dict from wizard selections."""
    llm_remote: list[dict[str, Any]] = [copy.deepcopy(upstream_llm_model)]
    if llm_pruner_model is not None:
        llm_remote = merge_model_entry(llm_remote, copy.deepcopy(llm_pruner_model))

    llm_section: dict[str, Any] = {"remote": llm_remote}
    if llm_minimum_tools is not None:
        llm_section["minimum_tools"] = llm_minimum_tools

    models: dict[str, Any] = {"llm": llm_section}

    if reranker_model is not None:
        rerankers_section: dict[str, Any] = {"remote": [copy.deepcopy(reranker_model)]}
        if reranker_minimum_tools is not None:
            rerankers_section["minimum_tools"] = reranker_minimum_tools
        models["rerankers"] = rerankers_section

    remote_defaults: dict[str, str] = {}
    if reranker_model is not None and "rerank" in pipeline:
        remote_defaults["reranking_model_nick"] = str(reranker_model["nick"])
    if llm_pruner_model is not None and "llm" in pipeline:
        remote_defaults["llm_model_nick"] = str(llm_pruner_model["nick"])

    defaults: dict[str, Any] = {
        "system_tool_policy": system_tool_policy,
        "mcp_tool_policy": mcp_tool_policy,
    }
    if "rerank" in pipeline:
        defaults["reranking_enabled"] = True
    if remote_defaults:
        defaults["remote"] = remote_defaults

    return {
        "defaults": defaults,
        "models": models,
        "pruning": {"pipeline": pipeline},
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


def print_proxy_urls(port: int, endpoints: list[str]) -> None:
    for endpoint in endpoints:
        print(f"http://localhost:{port}/{endpoint}")


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
        value = _prompt(text, default).strip()
        if value:
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
) -> list[str] | None:
    default = domain_match_default_string(provider, entry, upstreams=upstreams)
    raw = _prompt(
        "domain_match (comma-separated hostnames or API base URLs)",
        default,
    )
    return parse_domain_match(raw)


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


ChoiceT = TypeVar("ChoiceT", bound=str)


def _prompt_choice(text: str, choices: list[ChoiceT], default_index: int = 0) -> ChoiceT:
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
        kind_label=label,
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
    pricing = entry.get("pricing", {})
    if not isinstance(pricing, dict):
        pricing = {}
    in_default = pricing.get("input_cost_per_token")
    out_default = pricing.get("output_cost_per_token")
    in_per_token = float(in_default) if in_default is not None else None
    out_per_token = float(out_default) if out_default is not None else None
    in_cost = _prompt_pruner_input_cost(
        in_per_token,
        max_input_cost_per_token=max_input_cost_per_token,
    )
    out_cost = _prompt_cost_per_token(
        "output_cost_per_token",
        out_per_token if out_per_token is not None else in_cost,
    )
    domain_match = _prompt_domain_match(
        provider,
        entry,
        upstreams=domain_match_upstreams,
    )

    result: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "nick": nick,
        "max_tokens": max_tokens,
        "pricing": {
            "input_cost_per_token": in_cost,
            "output_cost_per_token": out_cost,
        },
    }
    if key_var is not None:
        result["key_var_name"] = key_var
    if domain_match is not None:
        result["domain_match"] = domain_match
    if allow_catalog_defaults and entry.get("base_url") is not None:
        result["base_url"] = copy.deepcopy(entry["base_url"])
    return result


def _prompt_custom_model(
    *,
    kind_label: str,
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
    in_cost = _prompt_pruner_input_cost(
        max_input_cost_per_token=max_input_cost_per_token,
    )
    out_cost = _prompt_cost_per_token("output_cost_per_token")
    result: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "nick": nick,
        "max_tokens": max_tokens,
        "pricing": {
            "input_cost_per_token": in_cost,
            "output_cost_per_token": out_cost,
        },
    }
    if key_var is not None:
        result["key_var_name"] = key_var
    if prompt_base_url or default_base_url is not None:
        base_url = normalize_base_url(
            _prompt(
                "base_url (may leave blank if one of the https://docs.litellm.ai/docs/providers selected)",
                default_base_url or "",
            ),
        )
        if base_url:
            result["base_url"] = base_url
    domain_match = _prompt_domain_match(
        provider,
        None,
        upstreams=domain_match_upstreams,
    )
    if domain_match is not None:
        result["domain_match"] = domain_match
    return result


def _pipeline_choice_labels(recommended_index: int) -> list[str]:
    base = ("rerank only", "llm only", "rerank and llm (both)")
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
    }
    normalized = choice.replace(" (recommended)", "")
    return pipeline_from_choice(mapping[normalized])


def _prompt_policy(label: str, default: ToolPolicy) -> ToolPolicy:
    return _prompt_choice(
        label,
        list(POLICY_CHOICES),
        default_index=POLICY_CHOICES.index(default),
    )


def _prompt_upstreams() -> tuple[list[dict[str, Any]], list[str]]:
    upstreams: list[dict[str, Any]] = []
    endpoints: list[str] = []
    print("Configure upstream API endpoints (kind + host URL).")
    while True:
        kind = _prompt("Upstream kind (e.g. anthropic, openai, gemini)", "anthropic")
        host_url = normalize_base_url(
            _prompt_required(
                "Host URL (required)",
                "https://api.anthropic.com",
            ),
        )
        upstreams.append(
            {
                "upstream": kind,
                "kind": kind,
                "host_url": host_url,
            },
        )
        if kind not in endpoints:
            endpoints.append(kind)
        if not _prompt_yes_no("Add another upstream?", default_yes=False):
            break
    return upstreams, endpoints


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
        secret = getpass.getpass(f"{key_var}: ")
        if secret:
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

    print("--- Upstream API endpoints ---")
    reverse_port = _prompt_int("Reverse proxy port", DEFAULT_REVERSE_PORT)
    print()
    upstreams, endpoints = _prompt_upstreams()

    print("\n--- Primary/Strong upstream LLM (for stats / cost tracking) ---")
    upstream_llm_model = _select_model_from_catalog(
        "llm",
        label="upstream LLM model",
        prompt_key_var=False,
        domain_match_upstreams=upstreams,
        filter_by_upstream_domains=True,
        custom_default_base_url=upstream_host_url_default(upstreams),
    )

    print_primary_too_cheap_warning(upstream_llm_model)

    pipeline = _prompt_pipeline(
        recommended_index=recommended_pipeline_default_index(upstream_llm_model),
    )

    max_pruner_input_cost = max_pruner_input_cost_per_token(upstream_llm_model)

    reranker_model: dict[str, Any] | None = None
    llm_pruner_model: dict[str, Any] | None = None
    reranker_minimum_tools: int | None = None
    llm_minimum_tools: int | None = None

    if "rerank" in pipeline:
        print("\n--- Reranker (weak pruning) model ---")
        reranker_model = _select_model_from_catalog(
            "rerankers",
            label="reranker model",
            prompt_key_var=True,
            max_input_cost_per_token=max_pruner_input_cost,
            prompt_custom_base_url=True,
        )
        reranker_minimum_tools = _prompt_int(
            "models.rerankers.minimum_tools",
            DEFAULT_RERANKER_MINIMUM_TOOLS,
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
        llm_minimum_tools = _prompt_int(
            "models.llm.minimum_tools",
            DEFAULT_LLM_MINIMUM_TOOLS,
        )

    if llm_minimum_tools is None:
        llm_minimum_tools = _prompt_int(
            "models.llm.minimum_tools",
            DEFAULT_LLM_MINIMUM_TOOLS,
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
        upstream_llm_model=upstream_llm_model,
        llm_minimum_tools=llm_minimum_tools,
        reranker_minimum_tools=reranker_minimum_tools,
        system_tool_policy=system_policy,
        mcp_tool_policy=mcp_policy,
        reverse_port=reverse_port,
        upstreams=upstreams,
        endpoints=endpoints,
        stats_db_path=stats_db,
    )

    save_user_config(config_path, overlay, apply_bundled_sections=True)
    print(f"\nWrote {config_path.expanduser()}")

    if _prompt_yes_no("Create a .env file for API keys?", default_yes=True):
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
    print("\tuv run cyt-rproxy serve")
    print("\n\nAnd point Claude Code at the proxy:\n")
    print('\texport ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"')
    print("\tclaude 'say hi' -p")
