"""Compact tier legend text for cyt-mcp and agent-skills injection blocks."""

from __future__ import annotations

from cyt.tiers.models import Tier

TOOL_TIER_LEGEND = (
    "Tool tiers: t4=full unfiltered inputSchema; "
    "t3=optional props pruned by prompt similarity (some may survive); "
    "t2=required props only, full schema via get-tool-definitions(tool-name); "
    "t1=description only, full schema via get-tool-definitions(tool-name); "
    "t0=dormant."
)

SKILL_TIER_LEGEND = (
    "Skill tiers: t4=full skill always included; "
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
