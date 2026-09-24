"""Integration tests: cyt-injection.mdc lifecycle via dev cyt-client wrapper + local hook.

Opt in with::

    uv run pytest src/tests/integration/test_cyt_injection_rules_lifecycle_integration.py --run-integration -q
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from cyt_client.rules_file import is_rules_placeholder_body, read_cursor_rules_injection
from tests.support.cursor_hook_shell_wrapper_fixtures import (
    cursor_dev_client_wrapper_command,
    run_hook_wrapper_direct,
)
from tests.support.cyt_injection_rules_lifecycle_fixtures import (
    before_submit_payload,
    build_hook_config,
    lifecycle_payload,
    load_lifecycle_scenario,
    materialize_lifecycle_workspace,
    patch_hook_environment,
    reset_catalog_state,
    run_local_hook_inject,
    write_substantive_rules,
)

pytestmark = pytest.mark.integration

_TOOLS_SCENARIO = load_lifecycle_scenario("before_submit_populates_tools")


class _LocalHookHandler(BaseHTTPRequestHandler):
    hook_config: dict[str, Any]

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        payload = json.loads(body)
        assert isinstance(payload, dict)
        response = run_local_hook_inject(payload, self.hook_config)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def lifecycle_integration_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, Any]]:
    reset_catalog_state()
    workspace = materialize_lifecycle_workspace(tmp_path)
    config = build_hook_config(workspace, db_path=tmp_path / "tiers-integration.db")
    patch_hook_environment(monkeypatch, workspace, config)
    return workspace, config


@pytest.fixture
def local_hook_server(lifecycle_integration_workspace: tuple[Path, dict[str, Any]]) -> str:
    _workspace, config = lifecycle_integration_workspace
    handler = type("BoundLocalHookHandler", (_LocalHookHandler,), {"hook_config": config})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/hook/inject"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    hooks_dir = tmp_path / "cursor" / "hooks"
    hooks_dir.mkdir(parents=True)
    command = cursor_dev_client_wrapper_command(hooks_dir, monkeypatch=monkeypatch)
    return Path(command)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix dev wrapper integration")
def test_wrapper_session_start_writes_lifecycle_placeholder(
    client_wrapper: Path,
    lifecycle_integration_workspace: tuple[Path, dict[str, Any]],
) -> None:
    workspace, _config = lifecycle_integration_workspace
    result = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=lifecycle_payload(workspace, "sessionStart"),
    )

    assert result.returncode == 0, result.stderr
    body = read_cursor_rules_injection(workspace)
    assert is_rules_placeholder_body(body)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix dev wrapper integration")
def test_wrapper_session_end_resets_substantive_rules_to_placeholder(
    client_wrapper: Path,
    lifecycle_integration_workspace: tuple[Path, dict[str, Any]],
) -> None:
    workspace, _config = lifecycle_integration_workspace
    write_substantive_rules(workspace)

    result = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=lifecycle_payload(workspace, "sessionEnd"),
    )

    assert result.returncode == 0, result.stderr
    body = read_cursor_rules_injection(workspace)
    assert is_rules_placeholder_body(body)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix dev wrapper integration")
def test_wrapper_before_submit_populates_rules_via_local_hook(
    client_wrapper: Path,
    lifecycle_integration_workspace: tuple[Path, dict[str, Any]],
    local_hook_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _config = lifecycle_integration_workspace
    monkeypatch.setenv("CYT_HOOK_URL", local_hook_server)

    result = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=before_submit_payload(workspace, str(_TOOLS_SCENARIO.raw["prompt"])),
    )

    assert result.returncode == 0, result.stderr
    rules_body = read_cursor_rules_injection(workspace)
    assert "<agent-tools" in rules_body
    for tool_name in _TOOLS_SCENARIO.raw["expected_tool_names"]:
        assert tool_name in rules_body


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix dev wrapper integration")
def test_full_wrapper_lifecycle_placeholder_inject_placeholder(
    client_wrapper: Path,
    lifecycle_integration_workspace: tuple[Path, dict[str, Any]],
    local_hook_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _config = lifecycle_integration_workspace
    monkeypatch.setenv("CYT_HOOK_URL", local_hook_server)

    start = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=lifecycle_payload(workspace, "sessionStart"),
    )
    assert start.returncode == 0, start.stderr
    assert is_rules_placeholder_body(read_cursor_rules_injection(workspace))

    submit = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=before_submit_payload(workspace, str(_TOOLS_SCENARIO.raw["prompt"])),
    )
    assert submit.returncode == 0, submit.stderr
    rules_after_prompt = read_cursor_rules_injection(workspace)
    assert "<agent-tools" in rules_after_prompt
    for tool_name in _TOOLS_SCENARIO.raw["expected_tool_names"]:
        assert tool_name in rules_after_prompt

    end = run_hook_wrapper_direct(
        client_wrapper,
        workspace=workspace,
        payload=lifecycle_payload(workspace, "sessionEnd"),
    )
    assert end.returncode == 0, end.stderr
    assert is_rules_placeholder_body(read_cursor_rules_injection(workspace))
