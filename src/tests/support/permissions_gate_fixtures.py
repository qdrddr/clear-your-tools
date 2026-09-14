"""Shared fixture loader for permissions disable/enable gate tests."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, cast

import pytest
import yaml

from cyt.common.agents import AgentName
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.permissions.editor import (
    disable_mcp_server,
    disable_mcp_tool,
    disable_skill,
    enable_mcp_server,
    enable_mcp_tool,
    enable_skill,
    load_permissions_lists,
)
from cyt.permissions.merge import effective_permissions, merged_hook_config
from cyt.permissions.paths import (
    PermissionAgentTarget,
    PermissionScope,
    normalize_agent,
)
from cyt.permissions.runtime import filter_catalog_tool_dicts, filter_skill_entries
from cyt.permissions.schema import EffectivePermissions
from cyt.skills.catalog import SkillEntryRef, clear_registry_cache
from tests.support.tiers_stats_fixtures import SKILLS_SOURCE_ROOT, load_tools_catalog

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "permissions_gate"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

ALL_SKILL_NAMES = ("create-hook", "context7", "database-shards")
ALL_TOOL_NAMES = (
    "context-mode_ctx_execute",
    "context-mode_ctx_search",
    "gitnexus_query",
)

_MCP_SERVER_TOOLS: dict[str, frozenset[str]] = {
    "context-mode": frozenset({"context-mode_ctx_execute", "context-mode_ctx_search"}),
    "gitnexus": frozenset({"gitnexus_query"}),
}

PermissionAction = Literal["disable", "enable"]
PermissionStepKind = Literal["skill", "mcp_server", "mcp_tool"]
PermissionMatch = Literal["name", "path"]


class _EditorKwargs(TypedDict):
    scope: PermissionScope
    agent_target: PermissionAgentTarget
    agent: AgentName
    global_config_path: Path
    workspace_root: Path


@dataclass(frozen=True)
class PermissionStep:
    action: PermissionAction
    scope: PermissionScope
    kind: PermissionStepKind
    target: str
    match: PermissionMatch = "name"


@dataclass(frozen=True)
class PermissionExpected:
    enabled_skills: frozenset[str]
    disabled_skills: frozenset[str]
    enabled_tools: frozenset[str]
    disabled_tools: frozenset[str]


@dataclass(frozen=True)
class ScopeDenyEmptyAssertion:
    scope: PermissionScope
    kind: Literal["skills", "mcp"]


@dataclass(frozen=True)
class PreseedDenyLayer:
    scope: PermissionScope
    kind: Literal["skills", "mcp"]
    deny: tuple[str, ...]


@dataclass(frozen=True)
class PermissionScenario:
    id: str
    agent: AgentName
    agent_target: PermissionAgentTarget
    steps: tuple[PermissionStep, ...]
    expected: PermissionExpected
    preseed_deny: tuple[PreseedDenyLayer, ...] = ()
    assert_scope_deny_empty: ScopeDenyEmptyAssertion | None = None


@dataclass(frozen=True)
class PermissionsGateFixturePack:
    workspace: Path
    global_config_path: Path
    workspace_config_path: Path
    skills_dir: Path
    catalog_dir: Path
    tools: list[dict[str, Any]]
    skill_fixture_names: tuple[str, ...]
    scenarios: tuple[PermissionScenario, ...]
    mcp_defs_path: Path


def _parse_permission_scope(raw: object) -> PermissionScope:
    text = str(raw).strip().lower()
    if text == "user":
        return "user"
    if text == "workspace":
        return "workspace"
    raise ValueError(f"invalid permission scope: {raw!r}")


def _parse_preseed_kind(raw: object) -> Literal["skills", "mcp"]:
    text = str(raw).strip().lower()
    if text == "skills":
        return "skills"
    if text == "mcp":
        return "mcp"
    raise ValueError(f"invalid preseed kind: {raw!r}")


def _parse_permission_action(raw: object) -> PermissionAction:
    text = str(raw).strip().lower()
    if text == "disable":
        return "disable"
    if text == "enable":
        return "enable"
    raise ValueError(f"invalid permission action: {raw!r}")


def _parse_step_kind(raw: object) -> PermissionStepKind:
    text = str(raw).strip().lower()
    if text == "skill":
        return "skill"
    if text == "mcp_server":
        return "mcp_server"
    if text == "mcp_tool":
        return "mcp_tool"
    raise ValueError(f"invalid permission step kind: {raw!r}")


def _parse_agent_target(raw: object) -> PermissionAgentTarget:
    text = str(raw or "all").strip().lower()
    if text == "cursor":
        return "cursor"
    if text == "claude":
        return "claude"
    if text == "codex":
        return "codex"
    if text == "all":
        return "all"
    raise ValueError(f"invalid agent_target: {raw!r}")


def _parse_agent_name(raw: object) -> AgentName:
    return cast(AgentName, normalize_agent(str(raw or "cursor")))


def _parse_expected(raw: dict[str, Any]) -> PermissionExpected:
    return PermissionExpected(
        enabled_skills=frozenset(str(name) for name in raw.get("enabled_skills", [])),
        disabled_skills=frozenset(str(name) for name in raw.get("disabled_skills", [])),
        enabled_tools=frozenset(str(name) for name in raw.get("enabled_tools", [])),
        disabled_tools=frozenset(str(name) for name in raw.get("disabled_tools", [])),
    )


def _parse_preseed_deny(raw: object) -> tuple[PreseedDenyLayer, ...]:
    if not isinstance(raw, list):
        return ()
    layers: list[PreseedDenyLayer] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        deny_raw = row.get("deny")
        if not isinstance(deny_raw, list):
            continue
        layers.append(
            PreseedDenyLayer(
                scope=_parse_permission_scope(row["scope"]),
                kind=_parse_preseed_kind(row["kind"]),
                deny=tuple(str(item) for item in deny_raw if str(item).strip()),
            ),
        )
    return tuple(layers)


def _parse_step(raw: dict[str, Any]) -> PermissionStep:
    match_raw = str(raw.get("match") or "name").strip().lower()
    match: PermissionMatch = "path" if match_raw == "path" else "name"
    return PermissionStep(
        action=_parse_permission_action(raw["action"]),
        scope=_parse_permission_scope(raw["scope"]),
        kind=_parse_step_kind(raw["kind"]),
        target=str(raw["target"]),
        match=match,
    )


def load_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[tuple[str, ...], tuple[PermissionScenario, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    skill_names_raw = payload.get("skill_fixture_names")
    if not isinstance(skill_names_raw, list):
        raise ValueError(f"{path}: expected skill_fixture_names array")
    scenarios_raw = payload.get("scenarios")
    if not isinstance(scenarios_raw, list):
        raise ValueError(f"{path}: expected scenarios array")

    scenarios: list[PermissionScenario] = []
    for row in scenarios_raw:
        if not isinstance(row, dict):
            continue
        assert_empty_raw = row.get("assert_scope_deny_empty")
        assert_empty: ScopeDenyEmptyAssertion | None = None
        if isinstance(assert_empty_raw, dict):
            assert_empty = ScopeDenyEmptyAssertion(
                scope=_parse_permission_scope(assert_empty_raw["scope"]),
                kind=_parse_preseed_kind(assert_empty_raw["kind"]),
            )
        steps_raw = row.get("steps")
        if not isinstance(steps_raw, list):
            raise ValueError(f"{path}: scenario {row.get('id')!r} missing steps")
        expected_raw = row.get("expected")
        scenarios.append(
            PermissionScenario(
                id=str(row["id"]),
                agent=_parse_agent_name(row.get("agent")),
                agent_target=_parse_agent_target(row.get("agent_target")),
                steps=tuple(_parse_step(step) for step in steps_raw if isinstance(step, dict)),
                expected=_parse_expected(
                    expected_raw if isinstance(expected_raw, dict) else {},
                ),
                preseed_deny=_parse_preseed_deny(row.get("preseed_deny")),
                assert_scope_deny_empty=assert_empty,
            ),
        )
    return tuple(str(name) for name in skill_names_raw), tuple(scenarios)


def skill_markdown_path(pack: PermissionsGateFixturePack, skill_name: str) -> Path:
    """Resolve fixture skill file path (flat ``name.md`` or ``name/SKILL.md``)."""
    flat = pack.skills_dir / f"{skill_name}.md"
    if flat.is_file():
        return flat
    nested = pack.skills_dir / skill_name / "SKILL.md"
    if nested.is_file():
        return nested
    raise FileNotFoundError(f"missing fixture skill {skill_name!r} under {pack.skills_dir}")


def _skill_path_for_target(pack: PermissionsGateFixturePack, skill_name: str) -> Path:
    nested = pack.skills_dir / skill_name
    if (nested / "SKILL.md").is_file():
        return nested
    return skill_markdown_path(pack, skill_name)


def _editor_kwargs(
    pack: PermissionsGateFixturePack,
    scenario: PermissionScenario,
    step: PermissionStep,
) -> _EditorKwargs:
    return {
        "scope": step.scope,
        "agent_target": scenario.agent_target,
        "agent": scenario.agent,
        "global_config_path": pack.global_config_path,
        "workspace_root": pack.workspace,
    }


def apply_step(
    pack: PermissionsGateFixturePack,
    scenario: PermissionScenario,
    step: PermissionStep,
) -> None:
    common = _editor_kwargs(pack, scenario, step)
    if step.kind == "skill":
        skill_path = _skill_path_for_target(pack, step.target) if step.match == "path" else None
        if step.action == "disable":
            disable_skill(
                step.target,
                skill_path=skill_path,
                **common,
            )
        else:
            enable_skill(
                step.target,
                skill_path=skill_path,
                **common,
            )
        return

    if step.kind == "mcp_server":
        if step.action == "disable":
            disable_mcp_server(step.target, **common)
        else:
            enable_mcp_server(step.target, **common)
        return

    if step.kind == "mcp_tool":
        if "/" not in step.target:
            raise ValueError(f"mcp_tool target must be SERVER/TOOL, got {step.target!r}")
        server, tool = step.target.split("/", 1)
        if step.action == "disable":
            disable_mcp_tool(server, tool, **common)
        else:
            enable_mcp_tool(server, tool, **common)
        return

    raise ValueError(f"Unknown permission step kind: {step.kind!r}")


def preseed_deny_layers(pack: PermissionsGateFixturePack, scenario: PermissionScenario) -> None:
    """Write pre-seeded deny entries before scenario steps (optional ``preseed_deny``)."""
    from cyt.config import save_user_config

    for layer in scenario.preseed_deny:
        from cyt.permissions.paths import permissions_config_path

        path = permissions_config_path(
            layer.scope,
            agent=scenario.agent,
            global_config_path=pack.global_config_path,
            workspace_root=pack.workspace,
        )
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(existing, dict):
            existing = {}
        if layer.kind == "skills":
            overlay = {"skills": {"permissions": {"deny": list(layer.deny)}}}
        else:
            overlay = {"tools": {"permissions": {"deny": list(layer.deny)}}}
        save_user_config(path, overlay)


def apply_steps(pack: PermissionsGateFixturePack, scenario: PermissionScenario) -> None:
    if scenario.preseed_deny:
        preseed_deny_layers(pack, scenario)
    for step in scenario.steps:
        apply_step(pack, scenario, step)


def effective_for_pack(
    pack: PermissionsGateFixturePack,
    *,
    agent: str | None = None,
) -> EffectivePermissions:
    global_cfg = yaml.safe_load(pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(global_cfg, dict):
        global_cfg = {}
    return effective_permissions(
        agent=agent or "cursor",
        global_config=global_cfg,
        workspace_root=pack.workspace,
    )


def merged_config_for_pack(
    pack: PermissionsGateFixturePack,
    *,
    agent: str = "cursor",
) -> dict[str, Any]:
    global_cfg = yaml.safe_load(pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(global_cfg, dict):
        global_cfg = {}
    merged = merged_hook_config(agent, global_config=global_cfg, workspace_root=pack.workspace)
    skills_cfg = merged.get("skills")
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    return set_hook_workspace_in_config(
        {
            **merged,
            "skills": {
                **skills_cfg,
                "enabled": True,
                "directories": [str(pack.skills_dir)],
                "catalog_dir": str(pack.catalog_dir),
            },
            "cache": {"skills_dir": str(pack.catalog_dir)},
        },
        pack.workspace,
    )


def _read_skill_frontmatter(skill_md: Path) -> str | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[: end + 4]


def skill_entries_for_pack(pack: PermissionsGateFixturePack) -> list[SkillEntryRef]:
    entries: list[SkillEntryRef] = []
    for skill_name in ALL_SKILL_NAMES:
        try:
            skill_md = skill_markdown_path(pack, skill_name)
        except FileNotFoundError:
            continue
        doc_id = skill_md.stem if skill_md.name.lower() != "skill.md" else skill_name
        frontmatter = _read_skill_frontmatter(skill_md)
        entries.append(
            SkillEntryRef(
                source_path=str(skill_md),
                doc_id=doc_id,
                content_sha256=f"hash-{skill_name}",
                cache_key=f"cache-{skill_name}",
                entry_dir=str(pack.skills_dir / skill_name),
                nodes_dir=str(pack.skills_dir / skill_name / "nodes"),
                chunk_dir=str(pack.skills_dir / skill_name / "chunks"),
                bm25_chunk_dir=str(pack.skills_dir / skill_name / "chunks"),
                pipeline="bm25",
                index_params_hash="hash",
                disk_backed=False,
                document={"structure": [], "frontmatter": frontmatter},
            ),
        )
    return entries


def _entry_policy_name(entry: SkillEntryRef) -> str:
    from cyt.permissions.runtime import skill_policy_name

    return skill_policy_name(entry)


def runtime_skill_names(
    pack: PermissionsGateFixturePack,
    *,
    agent: str = "cursor",
) -> tuple[set[str], set[str]]:
    effective = effective_for_pack(pack, agent=agent)
    entries = skill_entries_for_pack(pack)
    filtered = filter_skill_entries(entries, effective.skills.deny, base=pack.workspace)
    enabled = {_entry_policy_name(entry) for entry in filtered}
    all_names = {_entry_policy_name(entry) for entry in entries}
    disabled = all_names - enabled
    return enabled, disabled


def runtime_tool_names(
    pack: PermissionsGateFixturePack,
    *,
    agent: str = "cursor",
) -> tuple[set[str], set[str]]:
    effective = effective_for_pack(pack, agent=agent)
    filtered = filter_catalog_tool_dicts(pack.tools, effective.mcp.deny)
    enabled = {str(tool["name"]) for tool in filtered}
    all_names = {str(tool["name"]) for tool in pack.tools}
    disabled = all_names - enabled
    return enabled, disabled


def assert_runtime_skills(
    pack: PermissionsGateFixturePack,
    expected: PermissionExpected,
    *,
    agent: str = "cursor",
) -> None:
    enabled_skills, disabled_skills = runtime_skill_names(pack, agent=agent)
    assert enabled_skills == set(expected.enabled_skills), (
        f"enabled skills: got {sorted(enabled_skills)}, expected {sorted(expected.enabled_skills)}"
    )
    assert disabled_skills == set(expected.disabled_skills), (
        f"disabled skills: got {sorted(disabled_skills)}, "
        f"expected {sorted(expected.disabled_skills)}"
    )


def assert_runtime_tools(
    pack: PermissionsGateFixturePack,
    expected: PermissionExpected,
    *,
    agent: str = "cursor",
) -> None:
    enabled_tools, disabled_tools = runtime_tool_names(pack, agent=agent)
    assert enabled_tools == set(expected.enabled_tools), (
        f"enabled tools: got {sorted(enabled_tools)}, expected {sorted(expected.enabled_tools)}"
    )
    assert disabled_tools == set(expected.disabled_tools), (
        f"disabled tools: got {sorted(disabled_tools)}, expected {sorted(expected.disabled_tools)}"
    )


def expected_mcp_server_states(
    pack: PermissionsGateFixturePack,
    scenario: PermissionScenario,
) -> tuple[set[str], set[str]]:
    """Derive enabled/disabled MCP server names from effective server-level deny rules."""
    from cyt.permissions.match import is_mcp_server_denied

    effective = effective_for_pack(pack, agent=scenario.agent)
    enabled: set[str] = set()
    disabled: set[str] = set()
    for server in _MCP_SERVER_TOOLS:
        if is_mcp_server_denied(server, effective.mcp.deny):
            disabled.add(server)
        else:
            enabled.add(server)
    return enabled, disabled


def assert_expected_runtime(
    pack: PermissionsGateFixturePack,
    expected: PermissionExpected,
    *,
    agent: str = "cursor",
) -> None:
    assert_runtime_skills(pack, expected, agent=agent)
    assert_runtime_tools(pack, expected, agent=agent)


def assert_scope_deny_empty(
    pack: PermissionsGateFixturePack,
    assertion: ScopeDenyEmptyAssertion,
    *,
    agent: str = "cursor",
) -> None:
    from cyt.permissions.paths import permissions_config_path

    config_path = permissions_config_path(
        assertion.scope,
        agent=agent,
        global_config_path=pack.global_config_path,
        workspace_root=pack.workspace,
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    kind: Literal["mcp", "skills"] = "skills" if assertion.kind == "skills" else "mcp"
    deny, _allow = load_permissions_lists(raw, kind=kind, agent_target="all")
    assert deny == [], f"expected empty deny at {assertion.scope}/{assertion.kind}, got {deny!r}"


def materialize_fixture_pack(tmp_path: Path) -> PermissionsGateFixturePack:
    skill_fixture_names, scenarios = load_scenarios()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    for fixture_name in skill_fixture_names:
        source = SKILLS_SOURCE_ROOT / fixture_name
        if not source.is_file():
            raise FileNotFoundError(f"missing skill fixture: {source}")
        skill_name = fixture_name.replace(".md", "")
        target_dir = skills_dir / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / "SKILL.md")

    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    workspace_config_path = cyt_config_dir / "config.yaml"
    workspace_config_path.write_text(
        "skills:\n  directories:\n  - .agents/skills\n",
        encoding="utf-8",
    )
    (cyt_config_dir / "mcp-aggregator.yaml").write_text(
        "default_agent: cursor\n",
        encoding="utf-8",
    )
    mcp_defs_path = mcp_dir / "cursor.json"
    mcp_defs_path.write_text(
        '{"mcpServers": {"context-mode": {}, "gitnexus": {}}}',
        encoding="utf-8",
    )

    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True)
    global_config_path.write_text("agents: {}\n", encoding="utf-8")

    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()

    clear_registry_cache()
    return PermissionsGateFixturePack(
        workspace=workspace,
        global_config_path=global_config_path,
        workspace_config_path=workspace_config_path,
        skills_dir=skills_dir,
        catalog_dir=catalog_dir,
        tools=load_tools_catalog(),
        skill_fixture_names=skill_fixture_names,
        scenarios=scenarios,
        mcp_defs_path=mcp_defs_path,
    )


class GlobalConfigPathPack(Protocol):
    @property
    def global_config_path(self) -> Path: ...


def patch_global_config_path(
    monkeypatch: pytest.MonkeyPatch,
    pack: GlobalConfigPathPack,
) -> None:
    monkeypatch.setattr(
        "cyt.permissions.merge.DEFAULT_USER_CONFIG_PATH",
        pack.global_config_path,
    )
    monkeypatch.setattr(
        "cyt.config.DEFAULT_USER_CONFIG_PATH",
        pack.global_config_path,
    )
