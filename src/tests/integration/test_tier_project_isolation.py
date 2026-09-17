"""Integration tests: tier statistics isolated per git project in a shared DB."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers, get_tier_manager


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _tier_config(db_path: Path) -> dict:
    return {
        "tools": {
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(db_path)},
            },
            "hook": {
                "tools_from": ["cyt_mcp"],
            },
        },
        "pruning": {
            "tools": {
                "hook": {
                    "tools_from": ["cyt_mcp"],
                },
            },
        },
        "skills": {"tiers": {"mode": "shadow"}},
    }


def _tool_used_count(payload: dict, entity_id: str) -> float:
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        return 0.0
    by_tier = tools.get("by_tier")
    if not isinstance(by_tier, dict):
        return 0.0
    for items in by_tier.values():
        if not isinstance(items, list):
            continue
        for entity in items:
            if not isinstance(entity, dict):
                continue
            if entity.get("entity_id") != entity_id:
                continue
            stats = entity.get("stats")
            if isinstance(stats, dict):
                used = stats.get("used")
                if isinstance(used, (int, float)):
                    return float(used)
    return 0.0


def test_tier_stats_isolated_between_two_projects(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    db_path = tmp_path / "tier_state.db"
    base_config = _tier_config(db_path)
    entity_id = "cyt_mcp:test_tool"
    tool = stamp_tool_catalog_source(
        {"name": "test_tool", "cyt_catalog_source": "cyt_mcp"},
    )

    config_a = set_hook_workspace_in_config(base_config, repo_a)
    manager_a = get_tier_manager(config_a, workspace=repo_a)
    manager_a.record_tool_attempt(tool, config=config_a, success=True)
    manager_a.flush_pending(force=True)
    from cyt.tiers.models import EntityKind

    state_a = manager_a._states.get((EntityKind.TOOL, entity_id))
    assert state_a is not None
    assert state_a.stats.used >= 1.0

    config_b = set_hook_workspace_in_config(base_config, repo_b)
    manager_b = get_tier_manager(config_b, workspace=repo_b)
    manager_b.flush_pending(force=True)

    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: [tool],
    )
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: base_config)

    code_a = tiers_main(["stats", "--workspace", str(repo_a), "--json"])
    assert code_a == 0
    payload_a = json.loads(capsys.readouterr().out)

    code_b = tiers_main(["stats", "--workspace", str(repo_b), "--json"])
    assert code_b == 0
    payload_b = json.loads(capsys.readouterr().out)

    assert payload_a["project_id"] != payload_b["project_id"]
    assert Path(payload_a["root_path"]).resolve() == repo_a.resolve()
    assert Path(payload_b["root_path"]).resolve() == repo_b.resolve()
    assert _tool_used_count(payload_a, entity_id) >= 1.0
    assert _tool_used_count(payload_b, entity_id) == 0.0


def test_tiers_projects_lists_both_repositories(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    db_path = tmp_path / "tier_state.db"
    base_config = _tier_config(db_path)

    get_tier_manager(set_hook_workspace_in_config(base_config, repo_a), workspace=repo_a)
    get_tier_manager(set_hook_workspace_in_config(base_config, repo_b), workspace=repo_b)

    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: base_config)

    code = tiers_main(["projects", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    roots = {row["root_path"] for row in payload["projects"]}
    assert str(repo_a.resolve()) in roots
    assert str(repo_b.resolve()) in roots
