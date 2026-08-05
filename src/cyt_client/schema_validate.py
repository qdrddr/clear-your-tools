"""Stdlib-only JSON Schema validation for MCP tool input_schema."""

from __future__ import annotations

from typing import Any, cast


def validate_json_schema(
    value: object,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> tuple[bool, str]:
    """Return (ok, reason) with a JSON-pointer-style path on failure."""
    root = root_schema if root_schema is not None else schema
    return _validate(value, schema, root=root, path=path)


def _validate_ref(
    value: object,
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    resolved = _resolve_ref(schema["$ref"], root)
    if resolved is None:
        return False, f"{path}: unresolved $ref {schema['$ref']!r}"
    return _validate(value, resolved, root=root, path=path)


def _validate_type_and_enum(
    value: object,
    schema: dict[str, Any],
    *,
    path: str,
) -> tuple[bool, str]:
    if "const" in schema and value != schema["const"]:
        return False, f"{path}: value {value!r} does not match const {schema['const']!r}"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(_type_matches(value, t) for t in schema_type):
            return False, f"{path}: value {value!r} does not match type union {schema_type!r}"
    elif schema_type is not None:
        if not _type_matches(value, schema_type):
            return False, f"{path}: value {value!r} is not of type {schema_type!r}"

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        return False, f"{path}: value {value!r} not in enum {enum_values!r}"

    return True, ""


def _validate(
    value: object,
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    if "$ref" in schema:
        return _validate_ref(value, schema, root=root, path=path)

    for combinator in ("allOf", "anyOf", "oneOf"):
        if combinator in schema:
            return _validate_combinator(value, schema, combinator, root=root, path=path)

    ok, reason = _validate_type_and_enum(value, schema, path=path)
    if not ok:
        return False, reason

    if value is None:
        return True, ""

    schema_type = schema.get("type")
    if schema_type == "object" or (schema_type is None and isinstance(value, dict)):
        if not isinstance(value, dict):
            return False, f"{path}: expected object, got {type(value).__name__}"
        return _validate_object(cast(dict[str, Any], value), schema, root=root, path=path)

    if schema_type == "array" or (schema_type is None and isinstance(value, list)):
        if not isinstance(value, list):
            return False, f"{path}: expected array, got {type(value).__name__}"
        return _validate_array(value, schema, root=root, path=path)

    return True, ""


def _validate_all_of(
    value: object,
    subschemas: list[Any],
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    for sub in subschemas:
        if not isinstance(sub, dict):
            continue
        ok, reason = _validate(value, sub, root=root, path=path)
        if not ok:
            return False, reason
    return True, ""


def _validate_any_or_one_of(
    value: object,
    subschemas: list[Any],
    combinator: str,
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    matches = 0
    last_reason = ""
    for sub in subschemas:
        if not isinstance(sub, dict):
            continue
        ok, reason = _validate(value, sub, root=root, path=path)
        if ok:
            matches += 1
        else:
            last_reason = reason

    if combinator == "oneOf" and matches != 1:
        return False, f"{path}: expected exactly one oneOf match, got {matches}"
    if combinator == "anyOf" and matches == 0:
        return False, last_reason or f"{path}: no anyOf branch matched"
    return True, ""


def _validate_combinator(
    value: object,
    schema: dict[str, Any],
    combinator: str,
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    subschemas = schema.get(combinator)
    if not isinstance(subschemas, list) or not subschemas:
        return True, ""

    if combinator == "allOf":
        return _validate_all_of(value, subschemas, root=root, path=path)
    return _validate_any_or_one_of(value, subschemas, combinator, root=root, path=path)


def _validate_required_properties(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    path: str,
) -> tuple[bool, str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return True, ""

    for key in required:
        if not isinstance(key, str):
            continue
        if key not in value:
            return False, f"{path}: missing required property {key!r}"
        prop_value = value[key]
        if _is_empty_required_value(prop_value):
            return False, f"{path}.{key}: required property is empty"
    return True, ""


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    additional = schema.get("additionalProperties", False)
    for key, prop_value in value.items():
        if key in properties:
            prop_schema = properties[key]
            if isinstance(prop_schema, dict):
                ok, reason = _validate(
                    prop_value,
                    prop_schema,
                    root=root,
                    path=f"{path}.{key}",
                )
                if not ok:
                    return False, reason
        elif additional is False:
            return False, f"{path}: unknown property {key!r}"

    ok, reason = _validate_required_properties(value, schema, path=path)
    if not ok:
        return False, reason

    return True, ""


def _validate_array(
    value: list[Any],
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    items_schema = schema.get("items")
    if isinstance(items_schema, dict):
        for index, item in enumerate(value):
            ok, reason = _validate(item, items_schema, root=root, path=f"{path}[{index}]")
            if not ok:
                return False, reason
    return True, ""


def _is_empty_required_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _type_matches(value: object, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return True


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    node: object = root
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, dict) else None
