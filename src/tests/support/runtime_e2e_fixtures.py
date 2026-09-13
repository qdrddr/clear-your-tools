"""Shared helpers for opt-in runtime E2E tests (hook daemon + launch proxy)."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import yaml

from cyt.hook.daemon import (
    _spawn_hook_server,
    _wait_for_hook_server,
    _wait_for_port_free,
)
from cyt.hook.port import fetch_cyt_health, is_hook_server
from cyt.launch.proxy_guard import (
    LOCAL_HOST,
    STARTUP_TIMEOUT_SECONDS,
    ProxyGuard,
    _health_ok,
    _spawn_and_wait_for_healthy_proxy,
)
from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV
from cyt.tiers.models import Tier
from tests.support.paths import FIXTURES_DIR
from tests.support.skills_helpers import isolated_skills_agents_block
from tests.support.tier_behavior_fixtures import (
    SKILL_FIXTURE_NAMES,
    SKILLS_SOURCE_ROOT,
    live_tier_config,
    materialize_fixture_pack,
    seed_tool_tiers,
)

MCP_DEFINITIONS_FIXTURE = FIXTURES_DIR / "mcp_definitions_sample.json"


def ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCAL_HOST, 0))
        return int(sock.getsockname()[1])


def write_yaml_config(path: Path, config: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return path


@dataclass
class HookDaemonHandle:
    port: int
    base_url: str
    config_path: Path
    process: subprocess.Popen[Any]

    def health(self) -> dict[str, Any] | None:
        return fetch_cyt_health(self.port)

    def post_hook(self, payload: dict[str, Any], *, timeout: float = 30.0) -> httpx.Response:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            return client.post("/hook/inject", json=payload)

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        _wait_for_port_free(self.port, timeout=5.0)


@dataclass
class MockUpstreamHandle:
    port: int
    base_url: str
    captured: dict[str, Any]
    server: ThreadingHTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@dataclass
class RuntimeWorkspace:
    root: Path
    db_path: Path
    config_path: Path
    definitions_path: Path | None = None
    catalog_cache_dir: Path | None = None
    global_mcp_agg: Path | None = None
    global_mcp_defs: Path | None = None
    bm25_index_dir: Path | None = None


def _materialize_skills_workspace(root: Path) -> None:
    skills_root = root / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    for fixture_name in SKILL_FIXTURE_NAMES:
        source = SKILLS_SOURCE_ROOT / fixture_name
        doc_id = fixture_name.replace(".md", "")
        target_dir = skills_root / doc_id
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / "SKILL.md")
    cyt_config_dir = root / ".agents" / "cyt" / "config"
    cyt_config_dir.mkdir(parents=True, exist_ok=True)
    (cyt_config_dir / "config.yaml").write_text(
        "skills:\n  directories:\n  - .agents/skills\n",
        encoding="utf-8",
    )


def isolated_tools_hook_workspace(tmp_path: Path) -> RuntimeWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    definitions_path = tmp_path / "mcp_definitions.json"
    shutil.copy2(MCP_DEFINITIONS_FIXTURE, definitions_path)
    bm25_index_dir = tmp_path / "bm25"
    config_path = tmp_path / "cyt-config.yaml"
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "tools": {
                "enabled": True,
                "sequence": ["bm25"],
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(definitions_path),
                },
                "policy": {"minimum_tools": 1},
            },
        },
        "tools": {
            "enabled": True,
            "tiers": {"mode": "shadow"},
            "pipelines": {
                "bm25": {
                    "index_dir": str(bm25_index_dir),
                    "score_tool": 0.4,
                    "score_tool_enum": 0.1,
                    "prune_enums": True,
                },
            },
        },
        "skills": {"enabled": False},
        "models": {
            "bm25": {
                "index_dir": str(bm25_index_dir),
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
        "stats": {"database": {"path": str(tmp_path / "stats.db")}},
        "agents": isolated_skills_agents_block(),
    }
    write_yaml_config(config_path, config)
    return RuntimeWorkspace(
        root=root,
        db_path=root / "tier_state.db",
        config_path=config_path,
        definitions_path=definitions_path,
        bm25_index_dir=bm25_index_dir,
    )


def isolated_skills_hook_workspace(tmp_path: Path) -> RuntimeWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    _materialize_skills_workspace(root)
    catalog_dir = tmp_path / "skills-catalog"
    catalog_dir.mkdir()
    config_path = tmp_path / "cyt-config.yaml"
    config = {
        "cache": {"skills_dir": str(catalog_dir)},
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "tools": {"enabled": False},
        },
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(root / ".agents" / "skills")],
            "frontmatter_upper_limit": 0.4,
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
            "tiers": {"mode": "shadow"},
        },
        "agents": isolated_skills_agents_block(),
        "stats": {"database": {"path": str(tmp_path / "stats.db")}},
    }
    write_yaml_config(config_path, config)
    return RuntimeWorkspace(
        root=root,
        db_path=root / "tier_state.db",
        config_path=config_path,
    )


def isolated_live_tier_hook_workspace(tmp_path: Path) -> RuntimeWorkspace:
    pack = materialize_fixture_pack(tmp_path)
    seed_tool_tiers(
        pack,
        {
            "cyt_mcp:gitnexus_query": Tier.DORMANT,
            "cyt_mcp:context-mode_ctx_execute": Tier.EXTRA_HOT,
        },
    )
    config = live_tier_config(pack, kind="both")
    from cyt.cyt_mcp.catalog import apply_fetched_catalog

    apply_fetched_catalog(config, pack.tools)
    config.setdefault("pruning", {}).setdefault("tools", {})["enabled"] = True
    config.setdefault("pruning", {}).setdefault("tools", {})["sequence"] = ["bm25"]
    config["tools"]["pipelines"] = {
        "bm25": {
            "index_dir": str(tmp_path / "bm25"),
            "score_tool": 0.4,
            "score_tool_enum": 0.1,
            "prune_enums": True,
        },
    }
    config["models"] = {
        "bm25": {
            "index_dir": str(tmp_path / "bm25"),
            "mmap": False,
            "stem_language": "english",
            "stopwords": "en",
        },
    }
    workspace_config_path = pack.workspace / ".agents" / "cyt" / "config" / "config.yaml"
    workspace_config_path.write_text(
        yaml.dump(
            {
                "pruning": config.get("pruning"),
                "tools": config.get("tools"),
                "skills": config.get("skills"),
                "cache": config.get("cache"),
            },
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "cyt-config.yaml"
    write_yaml_config(config_path, config)
    return RuntimeWorkspace(
        root=pack.workspace,
        db_path=pack.db_path,
        config_path=config_path,
        catalog_cache_dir=pack.catalog_cache_dir,
        global_mcp_agg=pack.global_mcp_agg,
        global_mcp_defs=pack.global_mcp_defs,
        bm25_index_dir=tmp_path / "bm25",
    )


def cursor_hook_payload(*, workspace: Path, prompt: str) -> dict[str, Any]:
    workspace_text = str(workspace)
    return {
        "hook_event_name": "beforeSubmitPrompt",
        "prompt": prompt,
        "conversation_id": "runtime-e2e",
        "workspace_roots": [workspace_text],
        "model": "runtime-e2e",
    }


@contextmanager
def hook_daemon(
    config_path: Path,
    *,
    port: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[HookDaemonHandle]:
    chosen_port = port or ephemeral_port()
    env = {CYT_SKIP_KEYRING_ENV: "1", **(extra_env or {})}
    process = _spawn_hook_server(
        port=chosen_port,
        config_path=config_path,
        verbose=False,
        extra_env=env,
    )
    if not _wait_for_hook_server(chosen_port, process=process):
        process.terminate()
        pytest.fail(
            f"hook daemon failed to start on port {chosen_port} within {STARTUP_TIMEOUT_SECONDS}s",
        )
    handle = HookDaemonHandle(
        port=chosen_port,
        base_url=f"http://{LOCAL_HOST}:{chosen_port}",
        config_path=config_path,
        process=process,
    )
    try:
        yield handle
    finally:
        handle.terminate()


@contextmanager
def mock_upstream_server() -> Iterator[MockUpstreamHandle]:
    captured: dict[str, Any] = {}

    class _CapturingHandler(BaseHTTPRequestHandler):
        storage: ClassVar[dict[str, Any]]

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            self.storage["method"] = self.command
            self.storage["path"] = self.path
            self.storage["headers"] = dict(self.headers.items())
            self.storage["raw_body"] = body
            try:
                self.storage["json_body"] = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.storage["json_body"] = None
            payload = json.dumps(
                {
                    "id": "msg_runtime_e2e",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                },
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    _CapturingHandler.storage = captured
    port = ephemeral_port()
    server = ThreadingHTTPServer((LOCAL_HOST, port), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((LOCAL_HOST, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        server.shutdown()
        pytest.fail(f"mock upstream failed to bind on port {port}")

    handle = MockUpstreamHandle(
        port=port,
        base_url=f"http://{LOCAL_HOST}:{port}",
        captured=captured,
        server=server,
        thread=thread,
    )
    try:
        yield handle
    finally:
        handle.stop()


def proxy_launch_config(
    *,
    upstream_url: str,
    definitions_path: Path,
    bm25_index_dir: Path,
    stats_db: Path,
) -> dict[str, Any]:
    return {
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [
                        {
                            "endpoint": "anthropic",
                            "kind": "anthropic",
                            "url": upstream_url,
                        },
                    ],
                    "endpoints": ["anthropic"],
                    "inject_into_user_message": False,
                },
            },
        },
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "tools": {
                "enabled": True,
                "sequence": ["bm25"],
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(definitions_path),
                },
                "policy": {"minimum_tools": 1},
            },
        },
        "tools": {
            "enabled": True,
            "tiers": {"mode": "shadow"},
            "pipelines": {
                "bm25": {
                    "index_dir": str(bm25_index_dir),
                    "score_tool": 0.4,
                    "score_tool_enum": 0.1,
                    "prune_enums": True,
                },
            },
        },
        "skills": {"enabled": False},
        "models": {
            "bm25": {
                "index_dir": str(bm25_index_dir),
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
        "stats": {"database": {"path": str(stats_db)}},
        "agents": isolated_skills_agents_block(),
    }


@contextmanager
def launch_proxy(
    config_path: Path,
    *,
    port: int | None = None,
    launch_agent: str = "claude",
) -> Iterator[ProxyGuard]:
    chosen_port = port or ephemeral_port()
    guard = _spawn_and_wait_for_healthy_proxy(
        port=chosen_port,
        config_path=config_path,
        quiet=True,
        agent=launch_agent,
        debug=False,
        debug_dry_run=False,
        debug_strict=False,
        extra_env={CYT_SKIP_KEYRING_ENV: "1"},
    )
    if guard is None:
        pytest.fail(
            f"launch proxy failed to start on port {chosen_port} within {STARTUP_TIMEOUT_SECONDS}s",
        )
    try:
        yield guard
    finally:
        guard.terminate_if_started()
        _wait_for_port_free(chosen_port, timeout=5.0)


def assert_hook_server_health(health: dict[str, Any] | None) -> None:
    assert is_hook_server(health)


def assert_proxy_health(port: int) -> None:
    assert _health_ok(port)


__all__ = [
    "HookDaemonHandle",
    "MockUpstreamHandle",
    "RuntimeWorkspace",
    "assert_hook_server_health",
    "assert_proxy_health",
    "cursor_hook_payload",
    "ephemeral_port",
    "hook_daemon",
    "isolated_live_tier_hook_workspace",
    "isolated_skills_hook_workspace",
    "isolated_tools_hook_workspace",
    "launch_proxy",
    "mock_upstream_server",
    "proxy_launch_config",
    "write_yaml_config",
]
