"""Shared formatters for compact-JSON tool token accounting."""

from __future__ import annotations


def format_tool_token_line(tokens_in: int, tokens_out: int | None = None) -> str:
    msg = f"tool tokens (compact JSON): input={tokens_in}"
    if tokens_out is not None:
        saved = tokens_in - tokens_out
        pct = savings_percent(tokens_in, saved)
        msg += f", output={tokens_out}, saved={saved} ({pct:.1f}%)"
    return msg


def format_tool_token_arrow_line(
    tokens_in: int,
    tokens_out: int,
    *,
    tokens_saved: int | None = None,
) -> str:
    """Compact-JSON savings line using ``in -> out`` form (proxy debug logs)."""
    saved = tokens_in - tokens_out if tokens_saved is None else tokens_saved
    pct = savings_percent(tokens_in, saved)
    return (
        f"tool tokens (compact JSON): {tokens_in} -> {tokens_out} "
        f"(saved {saved}, {pct:.1f}%)"
    )


def format_agent_tools_token_lines(
    tokens_in: int,
    tokens_out: int | None = None,
    *,
    tool_count: int | None = None,
) -> list[str]:
    """Format token counts for the full ``<agent-tools>`` rulefile / additionalContext block."""
    if tool_count is not None:
        before = (
            f"\nTools before cleaning (compact JSON): tools={tool_count}, tokens={tokens_in}"
        )
    else:
        before = f"\nTools before cleaning (tokens): {tokens_in}"
    lines = [before]
    if tokens_out is not None:
        lines.append(
            f"Tools after cleaning agent-tools (compact JSON): tokens={tokens_out}"
        )
    return lines


def format_catalog_token_line(label: str, tool_count: int, tokens: int) -> str:
    return f"catalog tokens (compact JSON): {label} tools={tool_count} tokens={tokens}"


def format_frontend_stub_line(tool_count: int, tokens: int) -> str:
    return f"Frontend stubs (compact JSON): tools={tool_count}, tokens={tokens}"


def format_saved_tokens_line(
    tokens_in: int,
    tokens_out: int,
    frontend_tokens: int,
) -> str:
    saved = tokens_in - tokens_out - frontend_tokens
    pct = savings_percent(tokens_in, saved)
    return (
        f"Saved (tokens): {saved} ({pct:.1f}%) = "
        f"{tokens_in}-{tokens_out}+{frontend_tokens}"
    )


def savings_percent(tokens_in: int, saved: int) -> float:
    return (100.0 * saved / tokens_in) if tokens_in else 0.0


def build_preview_token_stats(
    *,
    tokens_in: int,
    tokens_out: int,
    tool_count_in: int | None = None,
    frontend_tool_count: int | None = None,
    frontend_tokens: int | None = None,
) -> dict[str, int | float]:
    saved = tokens_in - tokens_out
    stats: dict[str, int | float] = {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_saved": saved,
        "savings_percent": savings_percent(tokens_in, saved),
    }
    if tool_count_in is not None:
        stats["tool_count_in"] = tool_count_in
    if frontend_tool_count is not None and frontend_tokens is not None:
        net_output = tokens_out + frontend_tokens
        net_saved = tokens_in - net_output
        stats["frontend_tool_count"] = frontend_tool_count
        stats["frontend_tokens"] = frontend_tokens
        stats["net_tokens_out"] = net_output
        stats["net_tokens_saved"] = net_saved
        stats["net_savings_percent"] = savings_percent(tokens_in, net_saved)
    return stats


def format_preview_token_summary_lines(stats: dict[str, int | float]) -> list[str]:
    tool_count_in = stats.get("tool_count_in")
    lines = format_agent_tools_token_lines(
        int(stats["tokens_in"]),
        int(stats["tokens_out"]),
        tool_count=int(tool_count_in) if tool_count_in is not None else None,
    )
    if "frontend_tokens" in stats:
        lines.append(
            format_frontend_stub_line(
                int(stats["frontend_tool_count"]),
                int(stats["frontend_tokens"]),
            ),
        )
        lines.append(
            format_saved_tokens_line(
                int(stats["tokens_in"]),
                int(stats["tokens_out"]),
                int(stats["frontend_tokens"]),
            ),
        )
    return lines
