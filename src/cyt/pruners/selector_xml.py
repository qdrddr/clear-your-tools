"""Token attributes on LLM pruner selector XML (not proxy/hook injection)."""

from __future__ import annotations

from typing import Any


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


def wrap_agent_tools_bulk(inner: str, *, total_tokens: int) -> str:
    """Wrap decomposed tool selector chunks in ``<agent-tools total-tokens=…>``."""
    stripped = inner.strip()
    if not stripped:
        return ""
    return f"<agent-tools{selector_total_tokens_attr(total_tokens)}>\n{stripped}\n</agent-tools>"
