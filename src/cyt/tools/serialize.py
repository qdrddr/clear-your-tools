"""Hook-only JSON serialization with outer single-quote delimiters."""

from __future__ import annotations

import json
from typing import Any

from cyt.tool_examples.hash_utils import canonical_json


def canonicalize_json_value(value: object) -> object:
    """Return a deep copy with object keys in canonical JSON order."""
    return json.loads(canonical_json(value))


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


def _truncate_string(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return "." * max_chars
    return value[: max_chars - 3] + "..."


def _truncate_payload_values(value: object, max_chars: int) -> object:
    """Truncate string leaves in a payload; ``max_chars <= 0`` disables truncation."""
    if max_chars <= 0:
        return value
    if isinstance(value, str):
        return _truncate_string(value, max_chars)
    if isinstance(value, dict):
        return {key: _truncate_payload_values(item, max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_payload_values(item, max_chars) for item in value]
    return value


def format_example_line(payload: dict[str, Any], *, max_chars: int | None = None) -> str:
    """Serialize one successful tool-call payload as a dash-prefixed minimized JSON line."""
    display = canonicalize_json_value(payload)
    if max_chars is not None and max_chars > 0:
        display = _truncate_payload_values(display, max_chars)
    return f"- {minimize_json_single_quotes(display)}"


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
