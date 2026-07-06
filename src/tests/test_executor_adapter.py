"""Tests for executor Rust name adapter."""

from __future__ import annotations

from cyt.tools.executor_adapter import (
    EXECUTOR_RUST_PREFIX,
    executor_address_from_rust_name,
    prefix_tools_for_rust,
    restore_executor_addresses,
    rust_name_for_executor_address,
)


def test_prefix_and_restore_round_trip() -> None:
    address = "tools.demo.org.default.search"
    tools = [{"name": address, "description": "Search", "input_schema": {"type": "object"}}]

    prefixed, mapping = prefix_tools_for_rust(tools)
    assert prefixed[0]["name"] == rust_name_for_executor_address(address)
    assert mapping[prefixed[0]["name"]] == address

    restored = restore_executor_addresses(prefixed, mapping)
    assert restored is not None
    assert restored[0]["name"] == address


def test_executor_address_from_rust_name() -> None:
    address = "executor.coreTools.integrations.list"
    rust_name = f"{EXECUTOR_RUST_PREFIX}{address}"
    assert executor_address_from_rust_name(rust_name) == address
    assert executor_address_from_rust_name("mcp__legacy__tool") is None
