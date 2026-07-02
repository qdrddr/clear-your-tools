"""Hook-only JSON serialization with outer single-quote delimiters."""

from __future__ import annotations

import json


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
