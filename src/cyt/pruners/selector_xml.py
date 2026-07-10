"""Token attributes on LLM pruner selector XML (not proxy/hook injection)."""

from __future__ import annotations

import re
from typing import Any

SELECTOR_SOFT_BUDGET_TOOLS_TOTAL = 5000
SELECTOR_SOFT_BUDGET_SKILLS_TOTAL = 5000
SELECTOR_SOFT_BUDGET_MIN = 100

_SELECTOR_SOFT_BUDGET_NUMBER = re.compile(
    r"(You have a soft budget of )\d+( tokens to select the most relevant \w+\.?)",
)


def parse_cached_token_count(item: dict[str, Any]) -> int | None:
    """Read a cached ``token_count`` from a catalog or skill index row."""
    raw = item.get("token_count")
    if raw is None:
        return None
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


def selector_id_attr(selector_id: int) -> str:
    return f" id={selector_id}"


def selector_tokens_attr(token_count: int | None) -> str:
    if token_count is None or token_count <= 0:
        return ""
    return f" tokens={token_count}"


def selector_total_tokens_attr(total: int) -> str:
    if total <= 0:
        return ""
    return f" total-tokens={total}"


def format_selector_soft_budget_line(budget: int, *, target: str) -> str:
    return f"You have a soft budget of {budget} tokens to select the most relevant {target}."


def per_bulk_soft_budget(
    total_budget: int,
    num_bulks: int,
    *,
    min_budget: int = SELECTOR_SOFT_BUDGET_MIN,
) -> int:
    if num_bulks <= 1:
        return total_budget
    return max(min_budget, total_budget // num_bulks)


def replace_selector_soft_budget(prompt: str, budget: int) -> str:
    """Swap the soft-budget token count without interpreting other ``{…}`` in the prompt."""
    updated, count = _SELECTOR_SOFT_BUDGET_NUMBER.subn(
        rf"\g<1>{budget}\2",
        prompt,
        count=1,
    )
    return updated if count else prompt


def wrap_agent_tools_bulk(inner: str, *, total_tokens: int) -> str:
    """Wrap decomposed tool selector chunks in ``<agent-tools total-tokens=…>``."""
    stripped = inner.strip()
    if not stripped:
        return ""
    return f"<agent-tools{selector_total_tokens_attr(total_tokens)}>\n{stripped}\n</agent-tools>"
