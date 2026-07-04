"""Pruning result types shared across proxy and hook surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cyt.common.token_usage import StageTokenUsage

__all__ = ["PruneResult"]


@dataclass
class PruneResult:
    tools: list[dict[str, Any]] | None
    status: str
    query: str | None
    tools_in: int
    mcp_tools_in: int
    tools_out: int | None
    error: str | None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_saved: int | None = None
    tool_properties_count_in: int | None = None
    tool_properties_count_out: int | None = None
    tools_accepted: list[dict[str, Any]] | None = None
    tools_final: list[dict[str, Any]] | None = None
    pruning_model_tokens: dict[str, int] = field(default_factory=dict)
    pruning_token_usage: dict[str, StageTokenUsage] = field(default_factory=dict)
    decomposed: dict[str, int] = field(default_factory=dict)
    decomposed_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    decomposed_catalog: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "query": self.query,
            "tools_in": self.tools_in,
            "mcp_tools_in": self.mcp_tools_in,
            "tools_out": self.tools_out,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_saved": self.tokens_saved,
            "tool_properties_count_in": self.tool_properties_count_in,
            "tool_properties_count_out": self.tool_properties_count_out,
            "error": self.error,
            "decomposed": self.decomposed,
        }
        if self.decomposed_breakdown:
            out["decomposed_breakdown"] = self.decomposed_breakdown
        if self.pruning_model_tokens:
            out["pruning_model_tokens"] = self.pruning_model_tokens
        if self.pruning_token_usage:
            out["pruning_token_usage"] = {
                stage: {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                    "usage_source": usage.usage_source,
                }
                for stage, usage in self.pruning_token_usage.items()
            }
        if self.decomposed_catalog is not None:
            out["decomposed_catalog"] = self.decomposed_catalog
        return out
