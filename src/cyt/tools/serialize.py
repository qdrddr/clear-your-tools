"""Hook-only JSON serialization with outer single-quote delimiters."""

from __future__ import annotations

import json
from typing import Any


def minimize_json_single_quotes(value: object) -> str:
    """Minify JSON, then swap only structural double quotes to single quotes."""
    compact = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return _swap_json_delimiter_quotes(compact)


def _swap_json_delimiter_quotes(compact: str) -> str:
    out: list[str] = []
    i = 0
    n = len(compact)
    while i < n:
        char = compact[i]
        if char != '"':
            out.append(char)
            i += 1
            continue
        out.append("'")
        i += 1
        while i < n:
            inner = compact[i]
            if inner == "\\" and i + 1 < n:
                if compact[i + 1] == '"':
                    out.append('"')
                    i += 2
                    continue
                out.append(inner)
                out.append(compact[i + 1])
                i += 2
                continue
            if inner == '"':
                out.append("'")
                i += 1
                break
            out.append(inner)
            i += 1
    return "".join(out)


def format_example_line(payload: dict[str, Any], *, max_chars: int | None = None) -> str:
    """Serialize one successful tool-call payload as a dash-prefixed minimized JSON line."""
    line = f"- {minimize_json_single_quotes(payload)}"
    if max_chars is not None and len(line) > max_chars:
        return line[: max_chars - 3] + "..."
    return line


def format_examples_block(
    examples: list[dict[str, Any]],
    *,
    max_chars: int | None = None,
) -> str:
    """Wrap ranked call examples in an ``<examples>`` block.

    Returns an empty string when there are no non-empty example payloads so
    callers omit the ``<examples>`` tag entirely for that tool.
    """
    lines = [
        format_example_line(payload, max_chars=max_chars)
        for payload in examples
        if isinstance(payload, dict) and payload
    ]
    if not lines:
        return ""
    return "\n".join(["<examples>", *lines, "</examples>"])
