"""Adapt executor tool addresses for Rust MCP pruning (temporary mcp__exec__ prefix)."""

from __future__ import annotations

import copy
from typing import Any

from cyt.config import uses_executor_tool_catalog

EXECUTOR_RUST_PREFIX = "mcp__exec__"


def rust_name_for_executor_address(address: str) -> str:
    return f"{EXECUTOR_RUST_PREFIX}{address}"


def executor_address_from_rust_name(rust_name: str) -> str | None:
    if rust_name.startswith(EXECUTOR_RUST_PREFIX):
        return rust_name.removeprefix(EXECUTOR_RUST_PREFIX)
    return None


def prefix_tools_for_rust(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return prefixed tools and mapping rust_name -> raw executor address."""
    prefixed: list[dict[str, Any]] = []
    address_by_rust_name: dict[str, str] = {}
    for tool in tools:
        address = str(tool.get("name", "")).strip()
        if not address:
            continue
        rust_name = rust_name_for_executor_address(address)
        entry = copy.deepcopy(tool)
        entry["name"] = rust_name
        prefixed.append(entry)
        address_by_rust_name[rust_name] = address
    return prefixed, address_by_rust_name


def restore_executor_addresses(
    tools: list[dict[str, Any]] | None,
    address_by_rust_name: dict[str, str],
) -> list[dict[str, Any]] | None:
    if tools is None:
        return None
    restored: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name", "")).strip()
        address = address_by_rust_name.get(name)
        if address is None:
            address = executor_address_from_rust_name(name)
        if address is None:
            restored.append(copy.deepcopy(tool))
            continue
        entry = copy.deepcopy(tool)
        entry["name"] = address
        restored.append(entry)
    return restored


def is_executor_catalog_tool(name: str, config: dict[str, Any] | None = None) -> bool:
    """True when *name* is a raw executor address in executor hook catalog mode."""
    if config is not None and not uses_executor_tool_catalog(config):
        return False
    text = str(name).strip()
    return text.startswith(("tools.", "executor."))


def should_adapt_executor_tools_for_rust(config: dict[str, Any]) -> bool:
    return uses_executor_tool_catalog(config)
