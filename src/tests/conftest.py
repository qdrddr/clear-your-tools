"""Shared pytest configuration for ``src/tests``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

DEFAULT_LLM_PRUNE_AGENT = "cursor"


@pytest.fixture(autouse=True)
def _deterministic_indexer_cache() -> Iterator[None]:
    """Disable async cache writes and clear registry state between tests."""
    from cyt_indexer.cache import configure_memory_cache

    from cyt.skills.catalog import clear_registry_cache

    configure_memory_cache({"async_disk_writes": False, "lazy_registry": False})
    clear_registry_cache()
    yield
    clear_registry_cache()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--agent",
        action="store",
        default=DEFAULT_LLM_PRUNE_AGENT,
        choices=["cursor", "claude", "codex"],
        help="agent harness to simulate (cyt-client CYT_LAUNCH_AGENT + stdin shape)",
    )
    parser.addoption(
        "--rule",
        action="store",
        default=None,
        help="Cursor rules file path (workspace-relative or absolute); requires --agent cursor",
    )
