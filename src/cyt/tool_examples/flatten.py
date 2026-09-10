"""Flatten populated tool args into json_path → value pairs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FlattenedExample:
    json_path: str
    value: str
    value_type: str


_PATH_PREFIX = "inputSchema.properties"


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _serialize_value(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float, str)):
        return json.dumps(value, ensure_ascii=False)
    return None


def _should_skip_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def _key_redacted(key: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    for pattern in patterns:
        if pattern.search(key):
            return True
    return False


def flatten_args(
    args: dict[str, Any],
    *,
    redact_key_patterns: tuple[re.Pattern[str], ...] = (),
) -> list[FlattenedExample]:
    """Flatten top-level tool args into json_path rows."""
    out: list[FlattenedExample] = []
    _walk_object(args, _PATH_PREFIX, out, redact_key_patterns)
    return out


def _walk_object(
    obj: dict[str, Any],
    prefix: str,
    out: list[FlattenedExample],
    redact_patterns: tuple[re.Pattern[str], ...],
) -> None:
    for key, value in obj.items():
        if _key_redacted(str(key), redact_patterns):
            continue
        path = f"{prefix}.{key}"
        _walk_value(value, path, out, redact_patterns)


def _walk_value(
    value: Any,
    path: str,
    out: list[FlattenedExample],
    redact_patterns: tuple[re.Pattern[str], ...],
) -> None:
    if isinstance(value, dict):
        if _should_skip_value(value):
            return
        _walk_object(value, path, out, redact_patterns)
        return
    if isinstance(value, list):
        if _should_skip_value(value):
            return
        array_path = f"{path}.items[]"
        for item in value:
            if isinstance(item, dict):
                _walk_object(item, f"{path}.items[].properties", out, redact_patterns)
            else:
                _walk_scalar(item, array_path, out)
        return
    _walk_scalar(value, path, out)


def _walk_scalar(value: Any, path: str, out: list[FlattenedExample]) -> None:
    if _should_skip_value(value):
        return
    serialized = _serialize_value(value)
    if serialized is None:
        return
    out.append(
        FlattenedExample(
            json_path=path,
            value=serialized,
            value_type=_value_type(value),
        ),
    )
