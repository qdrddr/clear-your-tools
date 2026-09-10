"""Unit tests for tool example JSON-path flattening."""

from __future__ import annotations

import re

from cyt.tool_examples.flatten import flatten_args


def test_flatten_simple_scalar() -> None:
    pairs = flatten_args({"query": "bm25"})
    assert len(pairs) == 1
    assert pairs[0].json_path == "inputSchema.properties.query"
    assert pairs[0].value == '"bm25"'
    assert pairs[0].value_type == "string"


def test_flatten_nested_object() -> None:
    pairs = flatten_args({"options": {"limit": 10, "offset": 0}})
    paths = {item.json_path: item.value for item in pairs}
    assert paths["inputSchema.properties.options.limit"] == "10"
    assert paths["inputSchema.properties.options.offset"] == "0"


def test_flatten_array_of_objects() -> None:
    pairs = flatten_args({"items": [{"name": "bar"}]})
    paths = {item.json_path: item.value for item in pairs}
    assert paths["inputSchema.properties.items.items[].properties.name"] == '"bar"'


def test_flatten_skips_empty_values() -> None:
    pairs = flatten_args({"query": "", "limit": 0, "tags": [], "meta": {}})
    paths = {item.json_path for item in pairs}
    assert "inputSchema.properties.query" not in paths
    assert "inputSchema.properties.tags" not in paths
    assert "inputSchema.properties.meta" not in paths
    assert "inputSchema.properties.limit" in paths


def test_flatten_redacts_sensitive_keys() -> None:
    patterns = (re.compile(r"(?i)(password|secret|token|api[_-]?key)"),)
    pairs = flatten_args(
        {"query": "ok", "api_key": "secret-value", "password": "x"},  # pragma: allowlist secret
        redact_key_patterns=patterns,
    )
    paths = {item.json_path for item in pairs}
    assert "inputSchema.properties.query" in paths
    assert "inputSchema.properties.api_key" not in paths
    assert "inputSchema.properties.password" not in paths


def test_flatten_boolean_and_null() -> None:
    pairs = flatten_args({"enabled": True, "flag": False, "empty": None})
    by_path = {item.json_path: (item.value, item.value_type) for item in pairs}
    assert by_path["inputSchema.properties.enabled"] == ("true", "boolean")
    assert by_path["inputSchema.properties.flag"] == ("false", "boolean")
    assert by_path["inputSchema.properties.empty"] == ("null", "null")
