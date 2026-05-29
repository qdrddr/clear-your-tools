"""Interactive wizard for ``cyt-rproxy setup``."""

from __future__ import annotations

import copy
import getpass
import sys
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


def usd_per_million_to_per_token(usd_per_million: float) -> float:
    """Convert a price in USD per 1M tokens to per-token cost (scientific form in config)."""
    return usd_per_million / TOKENS_PER_MILLION


def per_token_to_usd_per_million(per_token: float) -> float:
    """Convert per-token cost to USD per 1M tokens for display."""
    return per_token * TOKENS_PER_MILLION


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
        return usd_per_million_to_per_token(value)
    return value


def domain_match_default_string(
    provider: str,
    entry: dict[str, Any] | None = None,
) -> str:
    """Default comma-separated hostnames for domain_match prompts."""
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
                    "upstreams": upstreams,
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


def _prompt_domain_match(provider: str, entry: dict[str, Any] | None) -> list[str] | None:
    default = domain_match_default_string(provider, entry)
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
) -> dict[str, Any]:
    catalog = _catalog_entries(kind)
    options = [f"{e.get('nick', '?')} ({e.get('provider')}/{e.get('name')})" for e in catalog]
    options.append("Custom…")
    choice = _prompt_choice(f"Select {label}", options)
    if choice != "Custom…":
        idx = options.index(choice)
        entry = copy.deepcopy(catalog[idx])
        return _confirm_model_fields(entry, allow_catalog_defaults=True)

    return _prompt_custom_model(kind_label=label)


def _confirm_model_fields(
    entry: dict[str, Any],
    *,
    allow_catalog_defaults: bool = False,
) -> dict[str, Any]:
    provider = str(entry.get("provider", ""))
    name = str(entry.get("name", ""))
    default_nick = str(entry.get("nick") or default_model_nick(provider, name))
    nick = _prompt("Model nick", default_nick)
    name = _prompt("Model name (https://docs.litellm.ai/docs/providers)", name)
    provider = _prompt("Provider (https://docs.litellm.ai/docs/providers)", provider)
    key_var = _prompt(
        "Environment variable for API key NAME (not the key itself, e.g. OPENAI_API_KEY), "
        "use key names from https://docs.litellm.ai/docs/providers",
        str(entry.get("key_var_name", "")),
    )
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
    in_cost = _prompt_cost_per_token("input_cost_per_token", in_per_token)
    out_cost = _prompt_cost_per_token(
        "output_cost_per_token",
        out_per_token if out_per_token is not None else in_cost,
    )
    domain_match = _prompt_domain_match(provider, entry)

    result: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "nick": nick,
        "key_var_name": key_var,
        "max_tokens": max_tokens,
        "pricing": {
            "input_cost_per_token": in_cost,
            "output_cost_per_token": out_cost,
        },
    }
    if domain_match is not None:
        result["domain_match"] = domain_match
    if allow_catalog_defaults and entry.get("base_url") is not None:
        result["base_url"] = copy.deepcopy(entry["base_url"])
    return result


def _prompt_custom_model(*, kind_label: str) -> dict[str, Any]:
    provider = _prompt("Provider (https://docs.litellm.ai/docs/providers)")
    name = _prompt("Model name (https://docs.litellm.ai/docs/providers)")
    nick = _prompt("Model nick", default_model_nick(provider, name))
    while not nick:
        nick = _prompt("Model nick (required)")
    key_var = _prompt(
        "Environment variable for API key NAME (not the key itself, e.g. OPENAI_API_KEY), "
        "use key names from https://docs.litellm.ai/docs/providers",
    )
    max_tokens = _prompt_int("max_tokens", 128000)
    in_cost = _prompt_cost_per_token("input_cost_per_token")
    out_cost = _prompt_cost_per_token("output_cost_per_token")
    domain_match = _prompt_domain_match(provider, None)
    result: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "nick": nick,
        "key_var_name": key_var,
        "max_tokens": max_tokens,
        "pricing": {
            "input_cost_per_token": in_cost,
            "output_cost_per_token": out_cost,
        },
    }
    if domain_match is not None:
        result["domain_match"] = domain_match
    return result


def _prompt_pipeline() -> list[str]:
    pipeline_labels = ("rerank only", "llm only", "rerank and llm (both)")
    choice = _prompt_choice("Pruning pipeline", list(pipeline_labels))
    mapping: dict[str, PipelineChoice] = {
        "rerank only": "rerank",
        "llm only": "llm",
        "rerank and llm (both)": "both",
    }
    return pipeline_from_choice(mapping[choice])


def _prompt_policy(label: str, default: ToolPolicy) -> ToolPolicy:
    return _prompt_choice(
        label,
        list(POLICY_CHOICES),
        default_index=POLICY_CHOICES.index(default),
    )


def _prompt_upstreams() -> tuple[list[dict[str, Any]], list[str]]:
    upstreams: list[dict[str, Any]] = []
    endpoints: list[str] = []
    print("Configure upstream API endpoints (kind + URL).")
    while True:
        kind = _prompt("Upstream kind (e.g. anthropic)", "anthropic")
        url = _prompt("Upstream URL", "https://api.anthropic.com")
        upstreams.append({"upstream": kind, "url": url, "kind": kind})
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

    pipeline = _prompt_pipeline()

    reranker_model: dict[str, Any] | None = None
    llm_pruner_model: dict[str, Any] | None = None
    reranker_minimum_tools: int | None = None
    llm_minimum_tools: int | None = None

    if "rerank" in pipeline:
        print("\n--- Reranker (weak pruning) model ---")
        reranker_model = _select_model_from_catalog("rerankers", label="reranker model")
        reranker_minimum_tools = _prompt_int(
            "models.rerankers.minimum_tools",
            DEFAULT_RERANKER_MINIMUM_TOOLS,
        )

    if "llm" in pipeline:
        print("\n--- LLM pruner (weak pruning) model ---")
        llm_pruner_model = _select_model_from_catalog("llm", label="LLM pruner model")
        llm_minimum_tools = _prompt_int(
            "models.llm.minimum_tools",
            DEFAULT_LLM_MINIMUM_TOOLS,
        )

    print("\n--- Primary/Strong upstream LLM (for stats / cost tracking) ---")
    upstream_llm_model = _select_model_from_catalog("llm", label="upstream LLM model")
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

    reverse_port = _prompt_int("Reverse proxy port", DEFAULT_REVERSE_PORT)
    print()
    upstreams, endpoints = _prompt_upstreams()

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

    save_user_config(config_path, overlay)
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
