"""Align hook injection schemas and examples with backend catalog shapes."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

from cyt.tiers.tool_token_materialization import CYT_BACKEND_INPUT_SCHEMA, input_schema_from_tool
from cyt_client.schema_validate import validate_json_schema


def backend_schema_from_tool(
    tool: dict[str, Any],
    *,
    full_tool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return unmodified backend schema from stamped key or *full_tool* fallback."""
    stamped = tool.get(CYT_BACKEND_INPUT_SCHEMA)
    if isinstance(stamped, dict):
        return stamped
    if isinstance(full_tool, dict):
        return input_schema_from_tool(full_tool)
    return input_schema_from_tool(tool)


def backend_schema_property_names(schema: dict[str, Any]) -> set[str]:
    return input_schema_properties(schema)


def backend_schema_required_names(schema: dict[str, Any]) -> list[str]:
    return schema_required_property_names(schema)


def backend_schema_is_empty(schema: dict[str, Any]) -> bool:
    return not backend_schema_property_names(schema)


def backend_schema_has_required(schema: dict[str, Any]) -> bool:
    return bool(backend_schema_required_names(schema))


def backend_schema_has_only_optionals(schema: dict[str, Any]) -> bool:
    return bool(backend_schema_property_names(schema)) and not backend_schema_has_required(schema)


def explicit_empty_object_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


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


def injection_needs_definitions_lookup(tool: dict[str, Any]) -> bool:
    """True when T2/T3 injection should emit a get-tool-definitions hint instead of schema."""
    tier = str(tool.get("cyt_injection_tier") or "").strip().lower()
    backend = backend_schema_from_tool(tool)
    injected = input_schema_from_tool(tool)

    if tier == "t2":
        if backend_schema_is_empty(backend):
            return False
        if backend_schema_has_required(backend):
            return False
        return True

    if tier == "t3":
        if backend_schema_is_empty(backend):
            return False
        if backend_schema_has_required(backend):
            return False
        return len(input_schema_properties(injected)) == 0

    return False


def injection_tier_needs_definitions_lookup(
    schema: dict[str, Any],
    tier: str | None,
) -> bool:
    """Legacy wrapper — prefer :func:`injection_needs_definitions_lookup` on the full tool."""
    tool = {"input_schema": schema, "cyt_injection_tier": tier}
    return injection_needs_definitions_lookup(tool)


def format_get_tool_definitions_hint(tool_name: str) -> str:
    from cyt.tools.serialize import minimize_json_single_quotes

    payload = minimize_json_single_quotes({"tool_name": tool_name})
    return f"Use `get-tool-definitions` with {payload}"


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


def _validate_t2_required_schema(
    tool: dict[str, Any],
    schema: dict[str, Any],
    backend: dict[str, Any],
) -> None:
    tier = str(tool.get("cyt_injection_tier") or "").strip().lower()
    if tier != "t2":
        return
    required = backend_schema_required_names(backend)
    if not required:
        return
    present = input_schema_properties(schema)
    missing = [name for name in required if name not in present]
    if missing:
        name = str(tool.get("name") or "")
        raise ValueError(
            f"T2 tool {name!r} missing required schema properties after merge: {missing}",
        )


def ensure_tool_injection_schema(
    tool: dict[str, Any],
    *,
    full_tool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure T2-T4 tools carry required schema fields; entangle examples to that schema."""
    out = copy.deepcopy(tool)
    tier = out.get("cyt_injection_tier")
    backend = backend_schema_from_tool(out, full_tool=full_tool)
    schema = input_schema_from_tool(out)
    if injection_tier_allows_required_schema(
        str(tier) if tier is not None else None,
    ):
        if backend:
            schema = ensure_required_properties_in_schema(schema, backend)
            out["input_schema"] = schema
        _validate_t2_required_schema(out, schema, backend)
    raw_examples = out.get("cyt_injection_examples")
    if isinstance(raw_examples, list) and schema:
        out["cyt_injection_examples"] = entangle_examples_with_schema(
            [item for item in raw_examples if isinstance(item, dict)],
            schema,
        )
    return out
