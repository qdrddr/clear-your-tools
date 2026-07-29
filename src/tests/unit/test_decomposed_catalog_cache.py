"""Tests for SWR decomposed catalog cache reads."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from cyt.config import load_config
from cyt.tools.catalog_cache import clear_decomposed_catalog_cache, get_tool_catalog_cache


@pytest.fixture(autouse=True)
def _reset_decomposed_cache() -> Iterator[None]:
    clear_decomposed_catalog_cache()
    yield
    clear_decomposed_catalog_cache()


def test_get_tool_catalog_cache_non_blocking_serves_stale_while_refreshing() -> None:
    config = load_config()
    entries = [{"name": "tool_a", "description": "A", "input_schema": {"type": "object"}}]
    first = get_tool_catalog_cache("executor", entries, [], config, blocking=True)
    assert first.catalog

    with patch("cyt.tools.catalog_cache.bulk_content_fingerprint", return_value="new-fp"):
        with patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh") as scheduled:
            result = get_tool_catalog_cache("executor", entries, [], config, blocking=False)

    assert result.catalog
    scheduled.assert_called_once()


def test_get_tool_catalog_cache_non_blocking_cold_start_builds_when_entries_present() -> None:
    config = load_config()
    entries = [{"name": "tool_a", "description": "A", "input_schema": {"type": "object"}}]
    with patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh") as scheduled:
        result = get_tool_catalog_cache("executor", entries, [], config, blocking=False)
    assert result.catalog
    assert result.cache_status != "empty"
    scheduled.assert_not_called()


def test_get_tool_catalog_cache_non_blocking_returns_empty_without_entries() -> None:
    config = load_config()
    with patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh") as scheduled:
        result = get_tool_catalog_cache("executor", [], [], config, blocking=False)
    assert result.cache_status == "empty"
    scheduled.assert_called_once()
