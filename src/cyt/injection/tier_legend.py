"""Compact tier legend text for cyt-mcp and agent-skills injection blocks."""

from __future__ import annotations

from cyt.tiers.models import Tier

TOOL_TIER_LEGEND = (
    "Tool tiers are grouped in <tier_tN>…</tier_tN> wrappers. "
    "t4=full unfiltered inputSchema; "
    "t3=optional props pruned by prompt similarity (required props always listed; some optionals may survive); "
    "t2=required props only (call get-tool-definitions(tool-name) for full optional schema); "
    "t1=description only, no input_schema (call get-tool-definitions(tool-name) before use); "
    "t0=dormant. Never guess argument property names — use listed input_schema keys or "
    "get-tool-definitions(tool-name)."
)

SKILL_TIER_LEGEND = (
    "Skill tiers are grouped in <tier_tN>…</tier_tN> wrappers. "
    "t4=full skill always included; "
    "t3=skill shell + survived chunks/nodes; "
    "t2=description + headers (most relevant, min 6); "
    "t1=description only; "
    "t0=dormant."
)


def injection_tier_attr(tier: str | Tier | None) -> str | None:
    """Normalize tier to lowercase t0-t4 for XML attributes."""
    if tier is None:
        return None
    if isinstance(tier, Tier):
        return f"t{int(tier)}"
    text = str(tier).strip().lower()
    if not text:
        return None
    if text.startswith("t") and len(text) == 2 and text[1].isdigit():
        return text
    if text.isdigit() and len(text) == 1:
        return f"t{text}"
    upper = text.upper()
    if upper.startswith("T") and len(upper) == 2 and upper[1].isdigit():
        return upper.lower()
    return text
