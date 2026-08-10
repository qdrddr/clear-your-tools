"""Shared pytest configuration for ``src/tests``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.credential_helpers import apply_ci_credential_stubs, install_test_pre_dotenv

DEFAULT_LLM_PRUNE_AGENT = "cursor"
INTEGRATION_SKIP_REASON = (
    "integration tests are manual-only (pytest -m integration --run-integration)"
)
QA_SKIP_REASON = "qa tests are manual-only (pytest -m qa --run-qa or ./scripts/local/tests/pytest-category.sh qa)"
RUNTIME_SKIP_REASON = (
    "runtime tests are manual-only (pytest -m runtime --run-runtime or "
    "./scripts/local/tests/pytest-category.sh runtime)"
)
RUNTIME_SPAWN_BLOCK_REASON = (
    "CYT hook daemon / launch proxy spawn blocked in automated tests "
    "(mark test @pytest.mark.runtime and run with --run-runtime or CYT_RUN_RUNTIME_TESTS=1)"
)

_SKIP_TXT_TEST_MARKERS = (
    "hook_skip_enabled",
    "skip_txt",
    "skips_hook_work_when_skip",
    "skip_emits_cursor",
    "skip_verbose_logs",
    "repair_pairing_skips_when_skip",
    "repair_pairing_from_mcp_runtime_skips_when_skip",
    "run_server_skips_pairing_when_skip",
)

_REPO_SKIP_TXT = Path(__file__).resolve().parents[2] / ".cursor" / "cyt" / "skip.txt"


def _test_exercises_skip_txt(test_name: str) -> bool:
    return any(marker in test_name for marker in _SKIP_TXT_TEST_MARKERS)


@pytest.fixture(autouse=True)
def _ignore_repo_skip_txt(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Developer ``skip.txt`` files must not disable unrelated unit tests."""
    from cyt_client import skip as skip_mod

    real_hook_skip = skip_mod.hook_skip_enabled
    real_paths = skip_mod.skip_hook_paths_for_payload
    exercises_skip = _test_exercises_skip_txt(request.node.name)
    ignored_skip_paths = {
        _REPO_SKIP_TXT.resolve(),
        skip_mod.GLOBAL_SKIP_PATH.resolve(),
    }

    def hook_skip(payload: dict | None = None) -> bool:
        if exercises_skip:
            return real_hook_skip(payload)
        return any(
            path.is_file()
            for path in real_paths(payload)
            if path.resolve() not in ignored_skip_paths
        )

    monkeypatch.setattr("cyt_client.skip.hook_skip_enabled", hook_skip)
    monkeypatch.setattr("cyt_client.cli.hook_skip_enabled", hook_skip)
    monkeypatch.setattr("cyt_client.pairing.hook_skip_enabled", hook_skip)


@pytest.fixture(autouse=True)
def _isolate_cyt_client_user_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not inherit developer hook overlays from ~/.config/cyt."""
    from cyt_client import config as cyt_client_config

    def isolated_resolve_config_path() -> Path:
        cwd_config = Path.cwd() / cyt_client_config.CWD_CONFIG_NAME
        if cwd_config.exists():
            return cwd_config
        return cyt_client_config.USER_CONFIG_PATH.expanduser().with_name(
            ".cyt-client-test-no-user-config.yaml",
        )

    monkeypatch.setattr(cyt_client_config, "resolve_config_path", isolated_resolve_config_path)


@pytest.fixture(autouse=True)
def _reset_cyt_client_pairing_sessions() -> Iterator[None]:
    """Pairing repair is session-scoped; clear module state between tests."""
    from cyt_client import pairing as cyt_client_pairing

    cyt_client_pairing._REPAIRED_SESSIONS.clear()
    yield
    cyt_client_pairing._REPAIRED_SESSIONS.clear()


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
    from cyt.cloudflare.catalog import clear_cloudflare_catalog_cache
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
        "cyt.cloudflare.cache_scheduler.start_cloudflare_cache_scheduler",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "cyt.tools.definitions_cache_scheduler.start_definitions_cache_scheduler",
        lambda *args, **kwargs: None,
    )

    def _clear_all_hook_catalog_state() -> None:
        clear_cloudflare_catalog_cache()
        clear_executor_catalog_cache()
        clear_mcpc_catalog_cache()
        clear_master_catalog_cache()
        clear_definitions_catalog_cache()
        clear_decomposed_catalog_cache()

    _clear_all_hook_catalog_state()
    yield
    _clear_all_hook_catalog_state()


@pytest.fixture(autouse=True)
def _block_cyt_runtime_spawns_unless_enabled(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Prevent accidental hook-daemon / launch-proxy subprocesses during pre-commit runs."""
    if request.node.get_closest_marker("runtime"):
        yield
        return
    if _runtime_tests_enabled(request.config):
        yield
        return

    import cyt.hook.daemon as hook_daemon
    import cyt.launch.proxy_guard as proxy_guard

    def blocked_hook_spawn(**kwargs: object) -> object:
        pytest.fail(RUNTIME_SPAWN_BLOCK_REASON)

    def blocked_proxy_spawn(**kwargs: object) -> object:
        pytest.fail(RUNTIME_SPAWN_BLOCK_REASON)

    monkeypatch.setattr(hook_daemon, "_spawn_hook_server", blocked_hook_spawn)
    monkeypatch.setattr(proxy_guard, "_spawn_proxy", blocked_proxy_spawn)
    yield


def _integration_tests_enabled(config: pytest.Config) -> bool:
    if config.getoption("--run-integration", default=False):
        return True
    value = os.environ.get("CYT_RUN_INTEGRATION_TESTS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _qa_tests_enabled(config: pytest.Config) -> bool:
    if config.getoption("--run-qa", default=False):
        return True
    value = os.environ.get("CYT_RUN_QA_TESTS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _runtime_tests_enabled(config: pytest.Config) -> bool:
    if config.getoption("--run-runtime", default=False):
        return True
    value = os.environ.get("CYT_RUN_RUNTIME_TESTS", "").strip().lower()
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
    parser.addoption(
        "--run-qa",
        action="store_true",
        default=False,
        help="run tests marked qa (manual BM25 smoke harnesses)",
    )
    parser.addoption(
        "--run-runtime",
        action="store_true",
        default=False,
        help="run tests marked runtime that may spawn cyt hook daemon or launch proxy",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _integration_tests_enabled(config):
        skip = pytest.mark.skip(reason=INTEGRATION_SKIP_REASON)
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)
    if not _qa_tests_enabled(config):
        skip = pytest.mark.skip(reason=QA_SKIP_REASON)
        for item in items:
            if item.get_closest_marker("qa"):
                item.add_marker(skip)
    if not _runtime_tests_enabled(config):
        skip = pytest.mark.skip(reason=RUNTIME_SKIP_REASON)
        for item in items:
            if item.get_closest_marker("runtime"):
                item.add_marker(skip)
