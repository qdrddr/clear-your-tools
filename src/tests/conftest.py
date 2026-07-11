"""Shared pytest configuration for ``src/tests``."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

DEFAULT_LLM_PRUNE_AGENT = "cursor"
INTEGRATION_SKIP_REASON = (
    "integration tests disabled (pass --run-integration or set CYT_RUN_INTEGRATION_TESTS=1)"
)

# Stub credentials in CI and local runs without ~/.config/cyt/.env so hook/cli tests
# do not exit on missing DEEPINFRA_API_KEY / EXECUTOR_TOKEN / OPENROUTER_API_KEY.
_CI_CREDENTIAL_STUBS: dict[str, str] = {
    "DEEPINFRA_API_KEY": "test-ci-stub",
    "EXECUTOR_TOKEN": "test-ci-stub",
    "OPENROUTER_API_KEY": "test-ci-stub",
}


@pytest.fixture(autouse=True)
def _ci_credential_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide dummy API keys when unset (GitHub Actions has no keyring or .env)."""
    for name, value in _CI_CREDENTIAL_STUBS.items():
        if not os.environ.get(name):
            monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _deterministic_indexer_cache() -> Iterator[None]:
    """Disable async cache writes and clear registry state between tests."""
    from cyt.indexer.cache import configure_memory_cache
    from cyt.skills.catalog import clear_registry_cache

    configure_memory_cache({"async_disk_writes": False, "lazy_registry": False})
    clear_registry_cache()
    yield
    clear_registry_cache()


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

