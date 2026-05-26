"""Token cost calculations from config.yaml model pricing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_STRONG_MODEL = "google/gemini-3-flash-preview"

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
    strong_model: str
    strong_input_rate: float

    @property
    def pruning_total_usd(self) -> float:
        return self.llm_input_usd + self.llm_output_usd + self.rerank_input_usd + self.rerank_output_usd

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


def strong_model_name(config: dict[str, Any]) -> str:
    stats = config.get("stats", {})
    if isinstance(stats, dict):
        configured = stats.get("strong_model")
        if isinstance(configured, str) and configured:
            return configured
    return DEFAULT_STRONG_MODEL


def empty_costs(config: dict[str, Any] | None = None) -> StatsCosts:
    model = strong_model_name(config or {})
    return StatsCosts(
        tools_saved_usd=0.0,
        llm_input_usd=0.0,
        llm_output_usd=0.0,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
        strong_model=model,
        strong_input_rate=0.0,
    )


def compute_stats_costs(
    totals: dict[str, int],
    stage_model_tokens: list[tuple[str, str | None, str, int]],
    config: dict[str, Any],
) -> StatsCosts:
    catalog = build_pricing_catalog(config)
    strong = strong_model_name(config)
    strong_pricing = lookup_pricing(catalog, strong)
    strong_input_rate = strong_pricing.input_cost_per_token if strong_pricing else 0.0

    tools_saved_usd = totals.get("tools_saved", 0) * strong_input_rate

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
        if token_type == "input":
            cost = count * pricing.input_cost_per_token
        elif token_type == "output":
            cost = count * pricing.output_cost_per_token
        else:
            cost = count * pricing.output_cost_per_token

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

    return StatsCosts(
        tools_saved_usd=tools_saved_usd,
        llm_input_usd=llm_input_usd,
        llm_output_usd=llm_output_usd,
        rerank_input_usd=rerank_input_usd,
        rerank_output_usd=rerank_output_usd,
        strong_model=strong,
        strong_input_rate=strong_input_rate,
    )


def format_usd(amount: float) -> str:
    if amount == 0:
        return "$0.000000"
    if abs(amount) >= 0.01:
        return f"${amount:.4f}"
    if abs(amount) >= 0.0001:
        return f"${amount:.6f}"
    return f"${amount:.9f}"
