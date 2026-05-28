"""Token cost calculations from config.yaml model pricing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_PROVIDER_PREFIXES = frozenset({"openrouter", "deepinfra", "ollama", "openai", "anthropic"})


@dataclass(frozen=True)
class ModelPricing:
    name: str
    input_cost_per_token: float
    output_cost_per_token: float
    kind: str  # "llm" | "reranker"


@dataclass
class StatsCosts:
    tools_saved_usd: float
    llm_input_usd: float
    llm_output_usd: float
    rerank_input_usd: float
    rerank_output_usd: float

    @property
    def pruning_total_usd(self) -> float:
        return (
            self.llm_input_usd
            + self.llm_output_usd
            + self.rerank_input_usd
            + self.rerank_output_usd
        )

    @property
    def net_savings_usd(self) -> float:
        return self.tools_saved_usd - self.pruning_total_usd


def normalize_model_name(name: str | None) -> str:
    if not name:
        return ""
    if "/" not in name:
        return name
    prefix, rest = name.split("/", 1)
    if prefix.lower() in _PROVIDER_PREFIXES:
        return rest
    return name


def _read_cost(pricing: dict[str, Any], key: str) -> float | None:
    value = pricing.get(key)
    if value is None and key == "output_cost_per_token":
        value = pricing.get('output_cost_per_token"')
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pricing_from_entry(entry: dict[str, Any], kind: str) -> ModelPricing | None:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    input_cost = _read_cost(pricing, "input_cost_per_token")
    output_cost = _read_cost(pricing, "output_cost_per_token")
    return ModelPricing(
        name=name,
        input_cost_per_token=input_cost or 0.0,
        output_cost_per_token=output_cost or 0.0,
        kind=kind,
    )


def _entry_provider_dns(entry: dict[str, Any]) -> str | None:
    domain_match = entry.get("domain_match")
    if isinstance(domain_match, list) and domain_match:
        return str(domain_match[0])
    base_url = entry.get("base_url")
    if base_url:
        hostname = urlparse(str(base_url)).hostname
        if hostname:
            return hostname
    return None


def _model_name_matches(entry: dict[str, Any], model_name: str) -> bool:
    name = entry.get("name")
    if not isinstance(name, str):
        return False
    if name == model_name:
        return True
    return normalize_model_name(name) == normalize_model_name(model_name)


def _llm_remote_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    entries = config.get("models", {}).get("llm", {}).get("remote", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def lookup_llm_pricing(
    config: dict[str, Any],
    model_name: str | None,
    provider_dns_name: str | None = None,
) -> ModelPricing | None:
    """Return LLM pricing for an upstream model, optionally disambiguated by provider DNS."""
    if not model_name:
        return None

    candidates = [entry for entry in _llm_remote_entries(config) if _model_name_matches(entry, model_name)]
    if provider_dns_name:
        by_dns = [
            entry
            for entry in candidates
            if _entry_provider_dns(entry) == provider_dns_name
        ]
        if by_dns:
            candidates = by_dns

    if len(candidates) == 1:
        return _pricing_from_entry(candidates[0], "llm")
    if len(candidates) > 1:
        return _pricing_from_entry(candidates[0], "llm")

    catalog = build_pricing_catalog(config)
    return lookup_pricing(catalog, model_name)


def build_pricing_catalog(config: dict[str, Any]) -> dict[str, ModelPricing]:
    """Index config model entries by normalized name."""
    catalog: dict[str, ModelPricing] = {}
    models = config.get("models", {})
    if not isinstance(models, dict):
        return catalog

    llm_remote = models.get("llm", {}).get("remote", [])
    if isinstance(llm_remote, list):
        for entry in llm_remote:
            if isinstance(entry, dict):
                parsed = _pricing_from_entry(entry, "llm")
                if parsed is not None:
                    catalog[normalize_model_name(parsed.name)] = parsed
                    catalog[parsed.name] = parsed

    rerank_remote = models.get("rerankers", {}).get("remote", [])
    if isinstance(rerank_remote, list):
        for entry in rerank_remote:
            if isinstance(entry, dict):
                parsed = _pricing_from_entry(entry, "reranker")
                if parsed is not None:
                    catalog[normalize_model_name(parsed.name)] = parsed
                    catalog[parsed.name] = parsed

    return catalog


def lookup_pricing(catalog: dict[str, ModelPricing], model_name: str | None) -> ModelPricing | None:
    if not model_name:
        return None
    direct = catalog.get(model_name)
    if direct is not None:
        return direct
    normalized = normalize_model_name(model_name)
    if normalized in catalog:
        return catalog[normalized]
    for key, pricing in catalog.items():
        if normalize_model_name(key) == normalized:
            return pricing
    return None


def empty_costs() -> StatsCosts:
    return StatsCosts(
        tools_saved_usd=0.0,
        llm_input_usd=0.0,
        llm_output_usd=0.0,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
    )


def _token_cost(pricing: ModelPricing, token_type: str, count: int) -> float:
    if token_type == "input":
        return count * pricing.input_cost_per_token
    return count * pricing.output_cost_per_token


def _accumulate_stage_cost(
    stage: str,
    token_type: str,
    cost: float,
    *,
    llm_input_usd: float,
    llm_output_usd: float,
    rerank_input_usd: float,
    rerank_output_usd: float,
) -> tuple[float, float, float, float]:
    if stage == "llm":
        if token_type == "input":
            llm_input_usd += cost
        elif token_type == "output":
            llm_output_usd += cost
    elif stage == "rerank":
        if token_type == "input":
            rerank_input_usd += cost
        elif token_type == "output":
            rerank_output_usd += cost
    return llm_input_usd, llm_output_usd, rerank_input_usd, rerank_output_usd


def compute_stats_costs(
    stage_model_tokens: list[tuple[str, str | None, str, int]],
    upstream_saved_tokens: list[tuple[str | None, str | None, int]],
    config: dict[str, Any],
) -> StatsCosts:
    tools_saved_usd = 0.0
    for model_name, provider_dns_name, count in upstream_saved_tokens:
        if count <= 0:
            continue
        pricing = lookup_llm_pricing(config, model_name, provider_dns_name)
        if pricing is None:
            continue
        tools_saved_usd += count * pricing.input_cost_per_token

    catalog = build_pricing_catalog(config)

    llm_input_usd = 0.0
    llm_output_usd = 0.0
    rerank_input_usd = 0.0
    rerank_output_usd = 0.0

    for stage, model_name, token_type, count in stage_model_tokens:
        if count <= 0:
            continue
        pricing = lookup_pricing(catalog, model_name)
        if pricing is None:
            continue
        cost = _token_cost(pricing, token_type, count)
        llm_input_usd, llm_output_usd, rerank_input_usd, rerank_output_usd = _accumulate_stage_cost(
            stage,
            token_type,
            cost,
            llm_input_usd=llm_input_usd,
            llm_output_usd=llm_output_usd,
            rerank_input_usd=rerank_input_usd,
            rerank_output_usd=rerank_output_usd,
        )

    return StatsCosts(
        tools_saved_usd=tools_saved_usd,
        llm_input_usd=llm_input_usd,
        llm_output_usd=llm_output_usd,
        rerank_input_usd=rerank_input_usd,
        rerank_output_usd=rerank_output_usd,
    )


def format_usd(amount: float) -> str:
    if amount == 0:
        return "$0.000000"
    if abs(amount) >= 0.01:
        return f"${amount:.4f}"
    if abs(amount) >= 0.0001:
        return f"${amount:.6f}"
    return f"${amount:.9f}"
