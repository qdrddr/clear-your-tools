"""Parametric unit tests for permissions disable/enable gate scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.permissions.match import is_catalog_tool_denied, is_skill_permission_denied
from cyt.permissions.paths import permissions_config_path
from cyt.permissions.runtime import skill_policy_name
from tests.support.permissions_gate_fixtures import (
    ALL_SKILL_NAMES,
    ALL_TOOL_NAMES,
    SCENARIOS_PATH,
    PermissionScenario,
    PermissionsGateFixturePack,
    apply_steps,
    assert_expected_runtime,
    assert_scope_deny_empty,
    effective_for_pack,
    load_scenarios,
    materialize_fixture_pack,
    patch_global_config_path,
    runtime_skill_names,
    runtime_tool_names,
    skill_entries_for_pack,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> PermissionsGateFixturePack:
    return materialize_fixture_pack(tmp_path)


def test_fixture_files_exist() -> None:
    assert SCENARIOS_PATH.is_file()
    _, scenarios = load_scenarios()
    assert len(scenarios) >= 18


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_permissions_gate_runtime_filters(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)
    apply_steps(fixture_pack, scenario)
    assert_expected_runtime(fixture_pack, scenario.expected, agent=scenario.agent)

    if scenario.assert_scope_deny_empty is not None:
        assert_scope_deny_empty(
            fixture_pack,
            scenario.assert_scope_deny_empty,
            agent=scenario.agent,
        )


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_permissions_gate_effective_deny_union(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)
    apply_steps(fixture_pack, scenario)

    effective = effective_for_pack(fixture_pack, agent=scenario.agent)
    for skill_name in scenario.expected.disabled_skills:
        entry = next(
            (
                item
                for item in skill_entries_for_pack(fixture_pack)
                if skill_policy_name(item) == skill_name
            ),
            None,
        )
        assert entry is not None
        assert is_skill_permission_denied(
            skill_name=skill_name,
            skill_path=entry.source_path,
            deny_entries=effective.skills.deny,
            base=fixture_pack.workspace,
        ), f"{skill_name} should be denied in effective permissions"

    for tool_name in scenario.expected.disabled_tools:
        assert is_catalog_tool_denied(tool_name, effective.mcp.deny), (
            f"{tool_name} should be denied in effective permissions"
        )


def _scope_disable_survives_enable(scope: str, scenario: PermissionScenario) -> bool:
    """True when a disable at *scope* is not undone by a later enable at the same scope."""
    disabled: set[tuple[str, str]] = set()
    for step in scenario.steps:
        if step.scope != scope:
            continue
        key = (step.kind, step.target)
        if step.action == "disable":
            disabled.add(key)
        elif step.action == "enable":
            disabled.discard(key)
    return bool(disabled)


def _scenarios_with_scope_disable(scope: str) -> list[str]:
    return [
        scenario.id
        for scenario in load_scenarios()[1]
        if _scope_disable_survives_enable(scope, scenario)
    ]


@pytest.mark.parametrize(
    "scenario_id",
    _scenarios_with_scope_disable("user"),
    ids=_scenarios_with_scope_disable("user"),
)
def test_permissions_gate_user_scope_writes_global_config(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)

    user_steps = [
        step for step in scenario.steps if step.scope == "user" and step.action == "disable"
    ]
    if not user_steps:
        pytest.skip("no user disable steps")

    apply_steps(fixture_pack, scenario)
    raw = yaml.safe_load(fixture_pack.global_config_path.read_text(encoding="utf-8")) or {}
    deny_lists: list[str] = []
    skills_deny = raw.get("skills", {}).get("permissions", {}).get("deny", [])
    tools_deny = raw.get("tools", {}).get("permissions", {}).get("deny", [])
    if isinstance(skills_deny, list):
        deny_lists.extend(str(item) for item in skills_deny)
    if isinstance(tools_deny, list):
        deny_lists.extend(str(item) for item in tools_deny)

    for step in user_steps:
        if step.kind == "skill" and step.match == "name":
            assert step.target in deny_lists or any(step.target in entry for entry in deny_lists)
        elif step.kind == "mcp_server":
            assert step.target in deny_lists
        elif step.kind == "mcp_tool":
            assert step.target in deny_lists


@pytest.mark.parametrize(
    "scenario_id",
    _scenarios_with_scope_disable("workspace"),
    ids=_scenarios_with_scope_disable("workspace"),
)
def test_permissions_gate_workspace_scope_writes_shared_config(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)

    workspace_steps = [
        step for step in scenario.steps if step.scope == "workspace" and step.action == "disable"
    ]
    if not workspace_steps:
        pytest.skip("no workspace disable steps")

    apply_steps(fixture_pack, scenario)
    expected_path = permissions_config_path(
        "workspace",
        agent=scenario.agent,
        global_config_path=fixture_pack.global_config_path,
        workspace_root=fixture_pack.workspace,
    )
    assert expected_path == fixture_pack.workspace_config_path
    assert expected_path.is_file()

    raw = yaml.safe_load(expected_path.read_text(encoding="utf-8")) or {}
    deny_lists: list[str] = []
    skills_deny = raw.get("skills", {}).get("permissions", {}).get("deny", [])
    tools_deny = raw.get("tools", {}).get("permissions", {}).get("deny", [])
    if isinstance(skills_deny, list):
        deny_lists.extend(str(item) for item in skills_deny)
    if isinstance(tools_deny, list):
        deny_lists.extend(str(item) for item in tools_deny)

    for step in workspace_steps:
        if step.kind == "skill":
            if step.match == "path":
                assert any("path:" in entry for entry in deny_lists)
            else:
                assert step.target in deny_lists or any(
                    step.target in entry for entry in deny_lists
                )
        elif step.kind == "mcp_server":
            assert step.target in deny_lists
        elif step.kind == "mcp_tool":
            assert step.target in deny_lists


def test_baseline_all_skills_and_tools_enabled(
    fixture_pack: PermissionsGateFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_global_config_path(monkeypatch, fixture_pack)
    enabled_skills, disabled_skills = runtime_skill_names(fixture_pack)
    enabled_tools, disabled_tools = runtime_tool_names(fixture_pack)
    assert enabled_skills == set(ALL_SKILL_NAMES)
    assert disabled_skills == set()
    assert enabled_tools == set(ALL_TOOL_NAMES)
    assert disabled_tools == set()
