"""Align hook injection schemas and examples with backend catalog shapes."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

from cyt.tiers.tool_token_materialization import input_schema_from_tool
from cyt_client.schema_validate import validate_json_schema


def input_schema_properties(schema: dict[str, Any]) -> set[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    return {str(key) for key in properties}


def schema_required_property_names(schema: dict[str, Any]) -> list[str]:
    """Return required property names that exist in *schema* properties."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required_raw = schema.get("required")
    if not isinstance(required_raw, list):
        return []
    return [str(name) for name in required_raw if str(name) in properties]


def injection_tier_needs_definitions_lookup(
    schema: dict[str, Any],
    tier: str | None,
) -> bool:
    """True when a T2 tool has no callable required properties in its injected schema."""
    if str(tier or "").strip().lower() != "t2":
        return False
    return len(schema_required_property_names(schema)) == 0


def format_get_tool_definitions_hint(tool_name: str) -> str:
    return f"use `get-tool-definitions` with `{{tool_name='{tool_name}'}}`"


def project_example_to_schema(
    example: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Keep only example keys present in *schema*; drop when validation fails."""
    if not example or not schema:
        return None
    allowed = input_schema_properties(schema)
    if not allowed:
        return None
    projected = {key: value for key, value in example.items() if key in allowed}
    if not projected:
        return None
    ok, _reason = validate_json_schema(projected, schema)
    if not ok:
        return None
    return projected


def entangle_examples_with_schema(
    examples: Sequence[object],
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter and trim examples so each matches the injected input_schema shape."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for example in examples:
        if not isinstance(example, dict):
            continue
        projected = project_example_to_schema(example, schema)
        if projected is None:
            continue
        key = json.dumps(projected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(projected)
    return out


def ensure_required_properties_in_schema(
    injection_schema: dict[str, Any],
    full_schema: dict[str, Any],
) -> dict[str, Any]:
    """Merge required property definitions from *full_schema* into *injection_schema*."""
    full = input_schema_from_tool({"input_schema": full_schema})
    out: dict[str, Any] = (
        copy.deepcopy(injection_schema)
        if injection_schema
        else {"type": "object", "properties": {}}
    )
    full_props = full.get("properties")
    if not isinstance(full_props, dict):
        return out
    props = out.setdefault("properties", {})
    if not isinstance(props, dict):
        props = {}
        out["properties"] = props
    required_raw = full.get("required")
    required = (
        [str(name) for name in required_raw if str(name) in full_props]
        if isinstance(required_raw, list)
        else []
    )
    for name in required:
        if name not in props and name in full_props:
            props[name] = copy.deepcopy(full_props[name])
    if required:
        existing = out.get("required")
        merged = list(
            dict.fromkeys([*required, *(existing if isinstance(existing, list) else [])]),
        )
        out["required"] = [name for name in merged if name in props]
    return out


def injection_tier_allows_required_schema(tier: str | None) -> bool:
    text = str(tier or "").strip().lower()
    return text in {"t2", "t3", "t4"}


def ensure_tool_injection_schema(
    tool: dict[str, Any],
    *,
    full_tool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure T2-T4 tools carry required schema fields; entangle examples to that schema."""
    out = copy.deepcopy(tool)
    tier = out.get("cyt_injection_tier")
    schema = input_schema_from_tool(out)
    if injection_tier_allows_required_schema(
        str(tier) if tier is not None else None,
    ):
        source = full_tool if isinstance(full_tool, dict) else out
        full_schema = input_schema_from_tool(source)
        if full_schema:
            schema = ensure_required_properties_in_schema(schema, full_schema)
            out["input_schema"] = schema
    raw_examples = out.get("cyt_injection_examples")
    if isinstance(raw_examples, list) and schema:
        out["cyt_injection_examples"] = entangle_examples_with_schema(
            [item for item in raw_examples if isinstance(item, dict)],
            schema,
        )
    return out
