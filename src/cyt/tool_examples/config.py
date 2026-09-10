"""Tool examples configuration resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config.sections import tools_at


@dataclass(frozen=True)
class ToolExamplesConfig:
    enabled: bool
    db_path: str
    post_tool_use: bool
    post_tool_matcher: str
    require_success: bool
    max_per_property: int
    max_value_chars: int
    full_call_examples: bool
    max_full_call_examples: int
    cross_schema_fallback: bool
    max_per_path: int
    min_per_path: int
    max_captures_per_tool: int
    min_captures_per_tool: int
    max_age_days: int
    redact_key_patterns: tuple[re.Pattern[str], ...]


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _examples_block(cfg: dict[str, Any]) -> dict[str, Any]:
    block = tools_at(cfg, "examples")
    if isinstance(block, dict):
        return block
    return {}


def tool_examples_db_path(cfg: dict[str, Any]) -> str:
    block = _examples_block(cfg)
    database = block.get("database")
    if isinstance(database, dict):
        path = database.get("path")
        if isinstance(path, str) and path.strip():
            return str(Path(path).expanduser())
    return str(Path("~/.config/cyt/tool_examples.db").expanduser())


def _compile_redact_patterns(raw: object) -> tuple[re.Pattern[str], ...]:
    if not isinstance(raw, list):
        return (re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization)"),)
    patterns: list[re.Pattern[str]] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            patterns.append(re.compile(item))
    if not patterns:
        return (re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization)"),)
    return tuple(patterns)


def tool_examples_config(cfg: dict[str, Any]) -> ToolExamplesConfig:
    block = _examples_block(cfg)
    capture = block.get("capture")
    capture_dict = capture if isinstance(capture, dict) else {}
    inject = block.get("inject")
    inject_dict = inject if isinstance(inject, dict) else {}
    retention = block.get("retention")
    retention_dict = retention if isinstance(retention, dict) else {}
    return ToolExamplesConfig(
        enabled=_bool(block.get("enabled"), False),
        db_path=tool_examples_db_path(cfg),
        post_tool_use=_bool(capture_dict.get("post_tool_use"), True),
        post_tool_matcher=str(
            capture_dict.get("post_tool_matcher") or "MCP:*|mcp__*",
        ),
        require_success=_bool(capture_dict.get("require_success"), True),
        max_per_property=_int(inject_dict.get("max_per_property"), 3),
        max_value_chars=_int(inject_dict.get("max_value_chars"), 120),
        full_call_examples=_bool(inject_dict.get("full_call_examples"), True),
        max_full_call_examples=_int(inject_dict.get("max_full_call_examples"), 2),
        cross_schema_fallback=_bool(inject_dict.get("cross_schema_fallback"), False),
        max_per_path=_int(retention_dict.get("max_per_path"), 20),
        min_per_path=_int(retention_dict.get("min_per_path"), 3),
        max_captures_per_tool=_int(retention_dict.get("max_captures_per_tool"), 50),
        min_captures_per_tool=_int(retention_dict.get("min_captures_per_tool"), 5),
        max_age_days=_int(retention_dict.get("max_age_days"), 90),
        redact_key_patterns=_compile_redact_patterns(block.get("redact_key_patterns")),
    )


def examples_active(cfg: dict[str, Any]) -> bool:
    return tool_examples_config(cfg).enabled
