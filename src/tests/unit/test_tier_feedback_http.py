"""Tests for hook daemon tier feedback endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt.tiers.flush_scheduler import reset_tier_flush_scheduler_for_tests
from cyt.tiers.manager import TierManager, _managers


def _sync_tier_database(db_path: Path) -> dict[str, object]:
    return {"path": str(db_path), "disk_flush_seconds": 0}


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()
    yield
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()


@pytest.fixture(autouse=True)
def _tier_feedback_isolated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: None,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_hook_tier_feedback_records_tool_used(project_root: Path, base_config: dict) -> None:
    from cyt.hook.http_server import hook_tier_feedback

    db_path = project_root / "tier_state.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow", "database": _sync_tier_database(db_path)}
    config["tools"] = tools

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "event": "tool_used",
                "workspace_root": str(project_root),
                "tool_name": "search",
                "catalog": "cyt_mcp",
                "args": {"query": "hello"},
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tier_feedback(request)
    assert response.status_code == 204

    manager = TierManager(project_root, str(db_path))
    try:
        state = manager._states.get(("tool", "cyt_mcp:search"))
        assert state is not None
        assert state.stats.used >= 1.0
    finally:
        manager.close()


@pytest.mark.asyncio
async def test_hook_tier_feedback_records_skill_used(project_root: Path, base_config: dict) -> None:
    from cyt.hook.http_server import hook_tier_feedback
    from cyt.tiers.adapters.skills import resolve_skill_entity_id

    db_path = project_root / "tier_state.db"
    skill_dir = project_root / ".agents" / "skills"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# Skill\n", encoding="utf-8")
    entity_id = resolve_skill_entity_id(str(skill_path.resolve()))
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow", "database": _sync_tier_database(db_path)}
    config["tools"] = tools

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "event": "skill_used",
                "workspace_root": str(project_root),
                "entity_id": str(skill_path.resolve()),
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tier_feedback(request)
    assert response.status_code == 204

    manager = TierManager(project_root, str(db_path))
    try:
        state = manager._states.get(("skill", entity_id))
        assert state is not None
        assert state.stats.used >= 1.0
        assert state.stats.used_without_injection >= 1.0
    finally:
        manager.close()


@pytest.mark.asyncio
async def test_hook_tier_feedback_skill_used_respects_last_injected(
    project_root: Path,
    base_config: dict,
) -> None:
    from cyt.common.paths import shorten_home_path
    from cyt.hook.http_server import hook_tier_feedback
    from cyt.tiers.adapters.skills import resolve_skill_entity_id

    db_path = project_root / "tier_state.db"
    skill_dir = project_root / ".agents" / "skills"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# Skill\n", encoding="utf-8")
    resolved_id = str(skill_path.resolve())
    entity_id = resolve_skill_entity_id(resolved_id)
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow", "database": _sync_tier_database(db_path)}
    config["tools"] = tools

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_skills_injected(
            [type("Match", (), {"file_path": shorten_home_path(resolved_id)})()],
            config,
        )
        _managers[str(project_root)] = manager

        request = MagicMock()
        request.client = MagicMock(host="127.0.0.1")
        request.body = AsyncMock(
            return_value=json.dumps(
                {
                    "event": "skill_used",
                    "workspace_root": str(project_root),
                    "entity_id": resolved_id,
                },
            ).encode(),
        )
        request.app = MagicMock()
        request.app.state.cyt_config = config

        with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
            response = await hook_tier_feedback(request)
        assert response.status_code == 204

        state = manager._states.get(("skill", entity_id))
        assert state is not None
        assert state.stats.used >= 1.0
        assert state.stats.used_without_injection == 0.0
    finally:
        _managers.pop(str(project_root), None)
        manager.close()


@pytest.mark.asyncio
async def test_hook_tier_feedback_records_failed_attempt(
    project_root: Path,
    base_config: dict,
) -> None:
    from cyt.hook.http_server import hook_tier_feedback

    db_path = project_root / "tier_state.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow", "database": _sync_tier_database(db_path)}
    config["tools"] = tools

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "event": "tool_used",
                "workspace_root": str(project_root),
                "tool_name": "search",
                "catalog": "cyt_mcp",
                "success": False,
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tier_feedback(request)
    assert response.status_code == 204

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(config, workspace=project_root)
    state = manager._states.get(("tool", "cyt_mcp:search"))
    assert state is not None
    assert state.stats.attempts >= 1.0
    assert state.stats.used == 0.0


@pytest.mark.asyncio
async def test_hook_tier_feedback_records_examples_on_success(
    project_root: Path,
    base_config: dict,
) -> None:
    from cyt.hook.http_server import hook_tier_feedback
    from cyt.tool_examples.store import ToolExamplesStore

    db_path = project_root / "tool_examples.db"
    tier_db = project_root / "tier_state.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow", "database": _sync_tier_database(tier_db)}
    examples = dict(tools.get("examples") or {})
    examples["enabled"] = True
    examples["database"] = {"path": str(db_path)}
    tools["examples"] = examples
    config["tools"] = tools

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "event": "tool_used",
                "workspace_root": str(project_root),
                "tool_name": "search",
                "catalog": "cyt_mcp",
                "success": True,
                "args": {"query": "hello"},
                "mcp_server": "codebase-memory",
                "bare_tool_name": "search",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tier_feedback(request)
    assert response.status_code == 204

    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(project_root))
        rows = store.list_captures(project_id, "codebase-memory", "search")
        assert rows
    finally:
        store.close()


@pytest.mark.asyncio
async def test_hook_tier_feedback_rejects_non_localhost() -> None:
    from cyt.hook.http_server import hook_tier_feedback

    request = MagicMock()
    request.client = MagicMock(host="203.0.113.1")
    response = await hook_tier_feedback(request)
    assert response.status_code == 403


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()
