"""Shared fixtures for cyt-mcp catalog resilience (reload, daemon restart, injection)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash
from cyt.hook.catalog_registry import RegisterStatus, clear_catalog_registry, register_catalog
from cyt.hook.workspace_config import set_hook_workspace_in_config

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_catalog_resilience"
WS_TOOLS_CATALOG_PATH = FIXTURES_DIR / "ws_tools_catalog.json"
USR_TOOLS_CATALOG_PATH = FIXTURES_DIR / "usr_tools_catalog.json"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"


@dataclass(frozen=True)
class CatalogResilienceScenario:
    id: str
    description: str
    raw: dict[str, Any]


def load_ws_tools_catalog(path: Path = WS_TOOLS_CATALOG_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def load_usr_tools_catalog(path: Path = USR_TOOLS_CATALOG_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def load_resilience_scenarios(path: Path = SCENARIOS_PATH) -> list[CatalogResilienceScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"{path}: expected scenarios array")
    loaded: list[CatalogResilienceScenario] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "").strip()
        if not scenario_id:
            continue
        loaded.append(
            CatalogResilienceScenario(
                id=scenario_id,
                description=str(item.get("description") or ""),
                raw=dict(item),
            ),
        )
    return loaded


def load_resilience_scenario(scenario_id: str) -> CatalogResilienceScenario:
    for scenario in load_resilience_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"no catalog resilience scenario for id {scenario_id!r}")


def materialize_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    (cyt_config_dir / "config.yaml").write_text(
        "skills:\n  directories:\n  - .agents/skills\n",
        encoding="utf-8",
    )
    (mcp_dir / "cursor.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    return workspace.resolve()


def register_ws_catalog(
    workspace: Path,
    tools: list[dict[str, Any]],
    *,
    catalog_layer: str = "ws",
    instance_id: str = "pid:test",
) -> None:
    register_layer_catalog(
        workspace,
        tools,
        catalog_layer=catalog_layer,
        instance_id=instance_id,
    )


def register_layer_catalog(
    workspace: Path,
    tools: list[dict[str, Any]],
    *,
    catalog_layer: str,
    instance_id: str = "pid:test",
) -> None:
    content_hash = raw_catalog_content_hash(tools)
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(workspace),
            "catalog_layer": catalog_layer,
            "instance_id": instance_id,
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.STORED


def cyt_mcp_hook_config(
    workspace: Path,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {"agent": "cursor"},
                },
                "sequence": ["bm25"],
            },
        },
        "skills": {"enabled": False},
        "tools": {
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(db_path or workspace / "tiers.db")},
            },
        },
    }
    return set_hook_workspace_in_config(config, workspace)


def daemon_status_payload_from_registry(
    registrations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {"registrations": registrations}


def patch_daemon_catalog_status(
    monkeypatch: Any,
    registrations: list[dict[str, Any]],
) -> None:
    """Simulate hook daemon /hook/catalog/status for CLI-side registry hydration."""

    payload = json.dumps(daemon_status_payload_from_registry(registrations)).encode("utf-8")

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(url: str, timeout: float = 1.5) -> _FakeResponse:  # noqa: ARG001
        if str(url).endswith("/hook/catalog/status"):
            return _FakeResponse(payload)
        raise OSError(f"unexpected urlopen in test: {url!r}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "cyt.hook.daemon_client.resolve_hook_path",
        lambda suffix="": f"http://127.0.0.1:8836{suffix}",
    )


def write_registry_disk_snapshot(
    snapshot_path: Path,
    workspace: Path,
    tools: list[dict[str, Any]],
    *,
    catalog_layer: str = "ws",
) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    content_hash = raw_catalog_content_hash(tools)
    snapshot_path.write_text(
        json.dumps(
            [
                {
                    "agent": "cursor",
                    "scope": "workspace",
                    "workspace_root": str(workspace),
                    "catalog_layer": catalog_layer,
                    "tools": tools,
                    "content_hash": content_hash,
                    "instance_id": "pid:snapshot",
                    "registered_at": 1.0,
                    "last_seen_at": 1.0,
                    "stale": False,
                },
            ],
        ),
        encoding="utf-8",
    )


def reset_catalog_state(*, purge_disk_snapshot: bool = True) -> None:
    from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
    from cyt.tools.master_catalog import clear_master_catalog_cache

    clear_catalog_registry(purge_disk_snapshot=purge_disk_snapshot)
    clear_cyt_mcp_catalog_cache()
    clear_master_catalog_cache()


def capture_registry_registrations() -> list[dict[str, Any]]:
    from cyt.hook.catalog_registry import list_catalog_registrations

    return list_catalog_registrations()


def register_dual_layer_catalog(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Register both ws and usr cyt-mcp catalog layers for one workspace."""
    ws_tools = load_ws_tools_catalog()
    usr_tools = load_usr_tools_catalog()
    register_layer_catalog(workspace, ws_tools, catalog_layer="ws")
    register_layer_catalog(workspace, usr_tools, catalog_layer="usr")
    return ws_tools, usr_tools


def write_usr_scope_disk_catalog(
    monkeypatch: Any,
    cache_dir: Path,
    *,
    usr_tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Persist user-scoped cyt-mcp tools to the global install-scope disk cache."""
    from cyt.cyt_mcp.catalog import (
        _CytMcpCacheKey,
        _global_scope_paths,
        _write_catalog_disk,
    )
    from cyt.cyt_mcp.catalog_disk import scope_config_fingerprint

    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir", lambda: cache_dir)
    tools = list(usr_tools if usr_tools is not None else load_usr_tools_catalog())
    global_agg, global_defs = _global_scope_paths("cursor")
    global_fp = scope_config_fingerprint(global_agg, global_defs)
    usr_key = _CytMcpCacheKey(agent="cursor", slug=global_fp, workspace="")
    _write_catalog_disk(usr_key, tools)
    return tools


def patch_tiers_stats_config(
    monkeypatch: Any,
    workspace: Path,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Point tiers stats CLI at an isolated hook config for *workspace*."""
    config = cyt_mcp_hook_config(workspace, db_path=db_path)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr("cyt.config.load_config", lambda: config)
    monkeypatch.setattr("cyt.tiers.cli.load_config", lambda: config)
    return config
