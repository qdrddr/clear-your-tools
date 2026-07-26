"""Shared pytest configuration for ``src/tests``."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.test_credential_helpers import apply_ci_credential_stubs, install_test_pre_dotenv

DEFAULT_LLM_PRUNE_AGENT = "cursor"
INTEGRATION_SKIP_REASON = (
    "integration tests are manual-only (pytest -m integration --run-integration)"
)


@pytest.fixture(autouse=True)
def _ci_credential_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide dummy API keys when unset (GitHub Actions has no keyring or .env)."""
    install_test_pre_dotenv(monkeypatch)
    apply_ci_credential_stubs(monkeypatch)


@pytest.fixture(autouse=True)
def _deterministic_indexer_cache() -> Iterator[None]:
    """Disable async cache writes and clear registry state between tests."""
    from cyt.indexer.cache import configure_memory_cache
    from cyt.skills.catalog import clear_registry_cache

    configure_memory_cache({"async_disk_writes": False, "lazy_registry": False})
    clear_registry_cache()
    yield
    clear_registry_cache()


@pytest.fixture(autouse=True)
def _isolate_hook_catalog_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop background catalog schedulers and clear in-memory hook caches."""
    from cyt.executor.http import clear_executor_catalog_cache
    from cyt.mcpc.catalog import clear_mcpc_catalog_cache
    from cyt.tools.catalog_cache import clear_decomposed_catalog_cache
    from cyt.tools.definitions_catalog import clear_definitions_catalog_cache
    from cyt.tools.master_catalog import clear_master_catalog_cache

    # Unit tests must not start live executor/MCPC/definitions refresh loops. A local
    # .env with EXECUTOR_TOKEN otherwise triggers real HTTP to localhost on every hook
    # catalog touch, and teardown waits on scheduler stop (appearing hung mid-suite).
    monkeypatch.setattr(
        "cyt.executor.cache_scheduler.start_executor_cache_scheduler",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "cyt.mcpc.cache_scheduler.start_mcpc_cache_scheduler",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "cyt.tools.master_cache_scheduler.start_master_cache_scheduler",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "cyt.tools.definitions_cache_scheduler.start_definitions_cache_scheduler",
        lambda *args, **kwargs: None,
    )

    def _clear_all_hook_catalog_state() -> None:
        clear_executor_catalog_cache()
        clear_mcpc_catalog_cache()
        clear_master_catalog_cache()
        clear_definitions_catalog_cache()
        clear_decomposed_catalog_cache()

    _clear_all_hook_catalog_state()
    yield
    _clear_all_hook_catalog_state()


def _integration_tests_enabled(config: pytest.Config) -> bool:
    if config.getoption("--run-integration", default=False):
        return True
    value = os.environ.get("CYT_RUN_INTEGRATION_TESTS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests marked integration that call real external APIs",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _integration_tests_enabled(config):
        return
    skip = pytest.mark.skip(reason=INTEGRATION_SKIP_REASON)
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)
