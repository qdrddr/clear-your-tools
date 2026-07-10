"""Token attributes on LLM pruner selector XML (not proxy/hook injection)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_DECOMPOSED_PREFIX = "schemas/decomposed/"

SELECTOR_SOFT_BUDGET_TOOLS_TOTAL = 2000
SELECTOR_SOFT_BUDGET_SKILLS_TOTAL = 2000
SELECTOR_SOFT_BUDGET_MIN = 100

_SELECTOR_SOFT_BUDGET_NUMBER = re.compile(
    r"(You have a soft budget of )\d+( tokens to select the most relevant \w+\.?)",
)


@dataclass(frozen=True)
class ToolSelectorTokenRow:
    selector_id: int
    tag: Literal["tool", "chunk"]
    tokens: int | None
    file_path: str | None


@dataclass(frozen=True)
class SkillSelectorBlockRow:
    file_path: str
    name: str | None
    total_tokens: int
    node_selector_ids: tuple[int, ...]


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


def decomposed_rel_path_from_tool_path(tool_path: Path) -> str | None:
    """Return ``schemas/decomposed/...`` relative path when present in *tool_path*."""
    normalized = tool_path.as_posix()
    index = normalized.find(_DECOMPOSED_PREFIX)
    if index == -1:
        return None
    return normalized[index:]


def token_count_from_decomposed_metadata(tool_json_path: Path) -> int | None:
    """Read cached ``token_count`` from neighboring ``metadata.json`` when present."""
    metadata_path = tool_json_path.parent / "metadata.json"
    if not metadata_path.is_file():
        return None

    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        files = raw.get("files")
        entries = files if isinstance(files, list) else [raw]
    else:
        return None

    rel_path = decomposed_rel_path_from_tool_path(tool_json_path)
    tool_name = tool_json_path.name
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        meta_path = str(entry.get("file_path", "")).strip()
        if not meta_path:
            continue
        if rel_path is not None and meta_path == rel_path:
            return parse_cached_token_count(entry)
        if Path(meta_path).name == tool_name:
            return parse_cached_token_count(entry)
    return None


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


def _format_token_value(tokens: int | None) -> str:
    return str(tokens) if tokens else "missing"


def format_tools_selector_token_metadata(
    token_rows: Sequence[ToolSelectorTokenRow],
    *,
    bulk_cached_totals: tuple[int, ...] = (),
) -> str:
    """Human-readable cached ``tokens`` / ``total-tokens`` for tools selector troubleshooting."""
    lines: list[str] = []
    if bulk_cached_totals:
        for index, total in enumerate(bulk_cached_totals, start=1):
            suffix = f" (agent-tools total-tokens={total})" if total > 0 else ""
            lines.append(f"bulk {index}{suffix}")
    elif token_rows:
        grand_total = sum(row.tokens or 0 for row in token_rows)
        if grand_total > 0:
            lines.append(f"agent-tools total-tokens={grand_total} (single batch)")

    for row in token_rows:
        lines.append(
            f"  <{row.tag} id={row.selector_id} tokens={_format_token_value(row.tokens)}>"
            + (f" file_path={row.file_path}" if row.file_path else ""),
        )

    if not lines:
        return "(no cached token metadata — re-index tools catalog to populate token_count)"
    return "\n".join(lines)


def format_skills_selector_token_metadata(
    metadata: dict[int, Any],
    blocks: Sequence[SkillSelectorBlockRow],
) -> str:
    """Human-readable cached ``tokens`` / ``total-tokens`` for skills selector troubleshooting."""
    lines: list[str] = []
    for selector_id in sorted(metadata):
        meta = metadata[selector_id]
        token_count = getattr(meta, "token_count", None)
        node_id = getattr(meta, "node_id", "?")
        file_path = getattr(meta, "file_path", "")
        lines.append(
            f"  <skill-node id={selector_id} tokens={_format_token_value(token_count)}> "
            f"node_id={node_id} path={file_path}",
        )

    if blocks:
        lines.append("")
        for index, block in enumerate(blocks, start=1):
            total = block.total_tokens if block.total_tokens > 0 else None
            lines.append(f"agent-skills[{index}] total-tokens={_format_token_value(total)}")
            lines.append(
                f"  <skill tokens={_format_token_value(total)}> "
                f"path={block.file_path}" + (f" name={block.name}" if block.name else ""),
            )

    if not lines:
        return "(no cached token metadata — re-index skills cache to populate token_count)"
    return "\n".join(lines)
