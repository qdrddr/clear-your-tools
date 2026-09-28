"""Schema propagation contract for cyt-mcp tools (required/optional property flow).

Type-1 vs Type-2
----------------
Type-2 ``tool_catalog`` JSONL entries come from the **unpruned master hook catalog**
(``emit_tool_catalog_session_log``). They are normalized records for preToolUse gating:
``name``, ``input_schema``, ``server_key``/``tool_name`` (cyt_mcp), optional ``description``.

Type-1 ``tool`` JSONL entries come from **prompt-pruned** tools that survive the session
gate (``gate_tools_for_session``). They record per-tool injection state for dedup/history.

Tier dual-schema (surviving tools)
----------------------------------
``cyt_backend_input_schema`` always holds the full backend shape (required + optional).
Tier ``input_schema`` materialization:

- T0/T1: ``{}`` (description-only or excluded)
- T2: required properties only
- T3/T4: required + optional (full backend shape)

After ``ensure_tool_injection_schema``, injected schema must contain all backend required
fields even when an upstream catalog row was partial (merged from master catalog peers).

Hard rules
----------
1. Never invent required properties not present on the backend tool.
2. Never drop backend required when a fuller source exists (disk, search_index, peers).
3. Never mix required/optional properties across tools.
4. Do not omit properties that should survive the current tier + prune stage.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from cyt.tools.injection_schema import input_schema_from_tool, schema_required_property_names

# Allowed top-level keys on normalized Type-2 cyt_mcp catalog records.
TYPE2_CYT_MCP_ALLOWED_KEYS = frozenset(
    {
        "name",
        "input_schema",
        "server_key",
        "tool_name",
        "description",
        "hash",
        "mcpc_session",
    },
)

# Raw FastMCP / runtime keys that must not appear on normalized Type-2 records.
TYPE2_FORBIDDEN_RAW_KEYS = frozenset(
    {
        "inputSchema",
        "parameters",
        "outputSchema",
        "output_schema",
        "annotations",
        "meta",
        "full_schema",
    },
)


class PropagationStage(str, Enum):
    BACKEND = "backend"
    MASTER_CATALOG = "master_catalog"
    FRONTEND_STUB = "frontend_stub"
    TIER_BACKEND_SCHEMA = "tier_backend_schema"
    TIER_INJECTED_SCHEMA = "tier_injected_schema"
    TYPE2_CATALOG_RECORD = "type2_catalog_record"
    TYPE1_TOOL_ENTRY = "type1_tool_entry"
    INJECTION_FRAGMENT = "injection_fragment"
    PRE_TOOL_GATE = "pre_tool_gate"


def required_names(schema: dict[str, Any]) -> set[str]:
    """Return required property names present in *schema*."""
    return set(schema_required_property_names(schema))


def optional_names(schema: dict[str, Any]) -> set[str]:
    """Return non-required property names defined in *schema*."""
    props = input_schema_from_tool({"input_schema": schema}).get("properties")
    properties = props if isinstance(props, dict) else {}
    return {str(key) for key in properties} - required_names(schema)


def property_names(schema: dict[str, Any]) -> set[str]:
    """Return all property names defined in *schema*."""
    props = input_schema_from_tool({"input_schema": schema}).get("properties")
    properties = props if isinstance(props, dict) else {}
    return {str(key) for key in properties}


def schema_from_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Extract input schema dict from a tool or Type-1/Type-2 log record."""
    return input_schema_from_tool(tool)


def assert_required_equal(
    stage: PropagationStage | str,
    actual_schema: dict[str, Any],
    expected_backend_schema: dict[str, Any],
    *,
    tool_name: str = "",
) -> None:
    """Assert *actual_schema* required set equals backend required set R(T)."""
    expected = required_names(expected_backend_schema)
    actual = required_names(actual_schema)
    label = f"{stage} for {tool_name!r}" if tool_name else str(stage)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        msg_parts = [f"required mismatch at {label}"]
        if missing:
            msg_parts.append(f"missing={sorted(missing)}")
        if extra:
            msg_parts.append(f"extra={sorted(extra)}")
        raise AssertionError("; ".join(msg_parts))


def assert_tier_injected_schema(
    tool: dict[str, Any],
    tier_label: str,
    backend_schema: dict[str, Any],
    *,
    tool_name: str = "",
) -> None:
    """Assert tier-scoped ``input_schema`` on *tool* matches contract for *tier_label*."""
    injected = schema_from_tool(tool)
    backend_required = required_names(backend_schema)
    backend_optional = optional_names(backend_schema)
    injected_required = required_names(injected)
    injected_optional = optional_names(injected)
    injected_all = property_names(injected)
    name = tool_name or str(tool.get("name") or "")

    assert_required_equal(
        PropagationStage.TIER_INJECTED_SCHEMA,
        injected,
        backend_schema,
        tool_name=name,
    )

    tier = tier_label.strip().upper()
    if tier == "T2":
        if injected_optional:
            raise AssertionError(
                f"T2 injected schema for {name!r} must not include optionals; "
                f"got {sorted(injected_optional)}",
            )
        if injected_all != backend_required:
            raise AssertionError(
                f"T2 injected schema for {name!r} must contain required only; "
                f"got properties {sorted(injected_all)}",
            )
    elif tier in {"T3", "T4"}:
        if backend_optional - injected_optional:
            missing = sorted(backend_optional - injected_optional)
            raise AssertionError(
                f"{tier} injected schema for {name!r} missing optionals {missing}",
            )
        if injected_all != backend_required | backend_optional:
            raise AssertionError(
                f"{tier} injected schema for {name!r} must match full backend shape; "
                f"got {sorted(injected_all)}, expected {sorted(backend_required | backend_optional)}",
            )


def assert_type2_record_shape(record: dict[str, Any], *, catalog: str = "cyt_mcp") -> None:
    """Assert a Type-2 catalog tool record is normalized (not a raw FastMCP dict)."""
    for forbidden in TYPE2_FORBIDDEN_RAW_KEYS:
        if forbidden in record:
            raise AssertionError(
                f"Type-2 record {record.get('name')!r} contains raw key {forbidden!r}",
            )
    if catalog == "cyt_mcp":
        extra = set(record) - TYPE2_CYT_MCP_ALLOWED_KEYS
        if extra:
            raise AssertionError(
                f"Type-2 cyt_mcp record {record.get('name')!r} has unexpected keys {sorted(extra)}",
            )
        server_key = str(record.get("server_key") or "").strip()
        tool_name = str(record.get("tool_name") or "").strip()
        if not server_key or not tool_name:
            raise AssertionError(
                f"Type-2 cyt_mcp record {record.get('name')!r} missing server_key/tool_name",
            )


def assert_backend_identity_preserved(tool: dict[str, Any], *, catalog: str = "cyt_mcp") -> None:
    """Assert cyt_mcp wire name maps to stable server_key + tool_name."""
    if catalog != "cyt_mcp":
        return
    wire_name = str(tool.get("name") or "").strip()
    server_key = str(tool.get("server_key") or "").strip()
    bare_name = str(tool.get("tool_name") or "").strip()
    if not wire_name or not server_key or not bare_name:
        raise AssertionError(f"cyt_mcp tool missing identity fields: {tool!r}")
    expected_wire = f"{server_key}_{bare_name}"
    if wire_name != expected_wire:
        raise AssertionError(
            f"cyt_mcp wire name mismatch: {wire_name!r} != {expected_wire!r} "
            f"(server_key={server_key!r}, tool_name={bare_name!r})",
        )


def assert_no_cross_tool_leakage(
    tools: list[dict[str, Any]],
    property_name: str,
    owner_tool_name: str,
) -> None:
    """Assert *property_name* appears only on *owner_tool_name* schemas/injection."""
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if name == owner_tool_name:
            continue
        schema = schema_from_tool(tool)
        props = property_names(schema)
        if property_name in props or property_name in required_names(schema):
            raise AssertionError(
                f"property {property_name!r} leaked to tool {name!r} (owner={owner_tool_name!r})",
            )


def assert_injection_fragment_properties(
    fragment: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
    forbidden: set[str] | None = None,
    tool_name: str = "",
) -> None:
    """Assert formatted injection *fragment* mentions expected property keys."""
    label = tool_name or "tool"
    for key in required:
        if f"'{key}'" not in fragment:
            raise AssertionError(f"injection fragment for {label!r} missing required key {key!r}")
    if optional:
        for key in optional:
            if f"'{key}'" not in fragment:
                raise AssertionError(f"injection fragment for {label!r} missing optional key {key!r}")
    if forbidden:
        for key in forbidden:
            if f"'{key}'" in fragment:
                raise AssertionError(
                    f"injection fragment for {label!r} must not include forbidden key {key!r}",
                )
