"""Token usage tracking for pruning pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field

TIKTOKEN_CL100K = "tiktoken:cl100k_base"


@dataclass
class StageTokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    usage_source: str = TIKTOKEN_CL100K
    request_id: str | None = None
    model_name: str | None = None
    provider_dns_name: str | None = None
    provider: str | None = None

    def merge(self, other: StageTokenUsage) -> StageTokenUsage:
        """Accumulate usage across bulks in the same stage."""
        reasoning: int | None
        if self.reasoning_tokens is None and other.reasoning_tokens is None:
            reasoning = None
        else:
            reasoning = (self.reasoning_tokens or 0) + (other.reasoning_tokens or 0)
        return StageTokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=reasoning,
            usage_source=other.usage_source or self.usage_source,
            request_id=other.request_id or self.request_id,
            model_name=other.model_name or self.model_name,
            provider_dns_name=other.provider_dns_name or self.provider_dns_name,
            provider=other.provider or self.provider,
        )


def empty_usage() -> StageTokenUsage:
    return StageTokenUsage()
