"""Tier-statistics-driven disable proposals for the permissions wizard."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ProposalTier = Literal["T0", "T1"]


@dataclass(frozen=True)
class ToolProposal:
    server: str
    tool: str
    entity_id: str
    tier: ProposalTier
    server_config_path: str | None = None
    server_config_line: int | None = None


@dataclass(frozen=True)
class SkillProposal:
    name: str
    path: Path
    entity_id: str
    tier: ProposalTier
    discovery_config_path: str | None = None
    discovery_directory: str | None = None


@dataclass(frozen=True)
class ServerRollupProposal:
    server: str
    tools: tuple[ToolProposal, ...]
    tier: ProposalTier


@dataclass(frozen=True)
class SkillDirRollupProposal:
    directory: Path
    directory_display: str
    skills: tuple[SkillProposal, ...]
    tier: ProposalTier


@dataclass
class TierProposalBundle:
    tier: ProposalTier
    tools: list[ToolProposal] = field(default_factory=list)
    skills: list[SkillProposal] = field(default_factory=list)
    server_rollups: list[ServerRollupProposal] = field(default_factory=list)
    skill_dir_rollups: list[SkillDirRollupProposal] = field(default_factory=list)
    leftover_tools: list[ToolProposal] = field(default_factory=list)
    leftover_skills: list[SkillProposal] = field(default_factory=list)


@dataclass(frozen=True)
class WizardConfig:
    min_candidates: int = 10
    idle_ms: int = 30 * 60 * 1000


def wizard_config_from_dict(config: dict[str, Any]) -> WizardConfig:
    block = config.get("permissions_cli") or {}
    wizard = block.get("wizard") if isinstance(block, dict) else {}
    if not isinstance(wizard, dict):
        wizard = {}
    tool_tiers = (config.get("pruning") or {}).get("tools") or {}
    tier_block = tool_tiers.get("tiers") or {}
    evaluation = tier_block.get("evaluation") or {}
    min_injections = int(evaluation.get("min_injections_before_reconsider") or 8)
    min_candidates = int(wizard.get("min_candidates") or min_injections)
    idle_minutes = wizard.get("idle_minutes")
    if idle_minutes is None:
        cache_epoch = tier_block.get("cache_epoch") or {}
        ttl_minutes = int(cache_epoch.get("prompt_cache_ttl_minutes") or 5)
        idle_minutes = max(ttl_minutes, 30)
    idle_ms = max(0, int(idle_minutes)) * 60 * 1000
    return WizardConfig(min_candidates=min_candidates, idle_ms=idle_ms)


def _entity_eligible(
    entity: dict[str, Any],
    *,
    target_tier: ProposalTier,
    wizard_cfg: WizardConfig,
    min_injections: int,
    now_ms: int,
) -> bool:
    base_tier = str(entity.get("base_tier") or "")
    if base_tier != target_tier:
        return False
    hints = entity.get("hints")
    if isinstance(hints, list) and "wake_candidate" in hints:
        return False
    stats = entity.get("stats")
    if not isinstance(stats, dict):
        return False
    used = float(stats.get("used") or 0)
    if used > 0:
        return False
    last_seen = int(stats.get("last_seen_ms") or 0)
    if last_seen > 0 and now_ms - last_seen < wizard_cfg.idle_ms:
        return False
    if target_tier == "T0":
        # T0 placement already signals low value; zero usage + idle is enough.
        return True
    candidates = int(stats.get("candidates") or 0)
    injected = int(stats.get("injected") or 0)
    if candidates < wizard_cfg.min_candidates and injected < min_injections:
        return False
    return True


def _collect_entities_for_tier(
    detail: dict[str, Any] | None,
    *,
    target_tier: ProposalTier,
    wizard_cfg: WizardConfig,
    min_injections: int,
    now_ms: int,
) -> list[dict[str, Any]]:
    if not isinstance(detail, dict):
        return []
    by_tier = detail.get("by_tier")
    if not isinstance(by_tier, dict):
        return []
    items = by_tier.get(target_tier)
    if not isinstance(items, list):
        return []
    eligible: list[dict[str, Any]] = []
    for entity in items:
        if isinstance(entity, dict) and _entity_eligible(
            entity,
            target_tier=target_tier,
            wizard_cfg=wizard_cfg,
            min_injections=min_injections,
            now_ms=now_ms,
        ):
            eligible.append(entity)
    return eligible


def _resolve_skill_root(path: Path, roots: list[Path]) -> Path | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    best: Path | None = None
    best_len = -1
    for root in roots:
        try:
            root_resolved = root.expanduser().resolve()
        except OSError:
            root_resolved = root.expanduser()
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        key_len = len(root_resolved.parts)
        if key_len > best_len:
            best = root_resolved
            best_len = key_len
    return best


def _skill_dir_display(directory: Path, workspace_root: Path) -> str:
    try:
        rel = directory.resolve().relative_to(workspace_root.resolve())
        return str(rel) if str(rel) != "." else "."
    except (OSError, ValueError):
        return str(directory)


def _tier_detail_dict(status_payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    detail = status_payload.get(key)
    return detail if isinstance(detail, dict) else None


def _build_tool_proposals(
    *,
    tool_entities: list[dict[str, Any]],
    catalog_tools: list[dict[str, Any]],
    scoped_config: dict[str, Any],
    project_root: Path,
    status_agent: str,
    target_tier: ProposalTier,
    effective_deny: tuple[str, ...],
) -> list[ToolProposal]:
    from cyt.permissions.match import split_catalog_tool_name
    from cyt.tiers.entity_origin import (
        parse_tool_entity_id,
        resolve_mcp_server_origin,
        resolve_tool_origin_fields,
    )

    tool_proposals: list[ToolProposal] = []
    for entity in tool_entities:
        entity_id = str(entity.get("entity_id") or "").strip()
        if not entity_id:
            continue
        origin = resolve_tool_origin_fields(
            entity_id,
            config=scoped_config,
            workspace_root=project_root,
            agent=status_agent,
        )
        server = str(origin.get("mcp_server") or "").strip()
        tool_name = str(origin.get("tool_name") or origin.get("display_name") or "").strip()
        if not server or not tool_name:
            _source, bare = parse_tool_entity_id(entity_id)
            tool_name = bare or tool_name
        if not server or not tool_name:
            for catalog_tool in catalog_tools:
                cname = str(catalog_tool.get("name") or "")
                parts = split_catalog_tool_name(cname)
                if parts is None:
                    continue
                if not tool_name or parts[1] == tool_name:
                    server = parts[0]
                    tool_name = parts[1]
                    break
        if not server or not tool_name:
            continue
        if not _tool_enabled(server, tool_name, effective_deny):
            continue
        _scope, mcp_config_path, mcp_line = resolve_mcp_server_origin(
            server,
            agent=status_agent,
            workspace_root=project_root,
        )
        tool_proposals.append(
            ToolProposal(
                server=server,
                tool=tool_name,
                entity_id=entity_id,
                tier=target_tier,
                server_config_path=mcp_config_path,
                server_config_line=mcp_line,
            ),
        )
    return tool_proposals


def _build_skill_proposals(
    *,
    skill_entities: list[dict[str, Any]],
    enabled_skills: list[Any],
    project_root: Path,
    status_agent: str,
    target_tier: ProposalTier,
    effective_deny: tuple[str, ...],
) -> list[SkillProposal]:
    from cyt.permissions.match import is_skill_permission_denied
    from cyt.tiers.entity_origin import resolve_skill_directory_origin

    enabled_by_path = {Path(item.path).resolve(): item for item in enabled_skills}
    skill_proposals: list[SkillProposal] = []
    for entity in skill_entities:
        entity_id = str(entity.get("entity_id") or "").strip()
        if not entity_id:
            continue
        source_path = entity.get("source_path") or entity_id
        try:
            path = Path(str(source_path)).expanduser().resolve()
        except OSError:
            path = Path(str(source_path)).expanduser()
        if path.is_dir():
            skill_md = path / "SKILL.md"
            path = skill_md if skill_md.is_file() else path
        inv = enabled_by_path.get(path.resolve()) if path.exists() else None
        if inv is None:
            name = str(entity.get("display_name") or entity.get("name") or path.parent.name)
            if path.resolve() not in enabled_by_path and is_skill_permission_denied(
                skill_name=name,
                skill_path=path,
                deny_entries=effective_deny,
                base=project_root,
            ):
                continue
        else:
            name = inv.name
            path = Path(inv.path)
        discovery_config_path, discovery_directory = resolve_skill_directory_origin(
            path,
            agent=status_agent,
            workspace_root=project_root,
        )
        skill_proposals.append(
            SkillProposal(
                name=name,
                path=path,
                entity_id=entity_id,
                tier=target_tier,
                discovery_config_path=discovery_config_path,
                discovery_directory=discovery_directory,
            ),
        )
    return skill_proposals


def _partition_tool_proposals(
    tool_proposals: list[ToolProposal],
    *,
    catalog_tools: list[dict[str, Any]],
    effective_deny: tuple[str, ...],
    target_tier: ProposalTier,
) -> tuple[list[ServerRollupProposal], list[ToolProposal]]:
    from cyt.permissions.match import split_catalog_tool_name

    server_rollups: list[ServerRollupProposal] = []
    leftover_tools: list[ToolProposal] = []
    tools_by_server: dict[str, list[ToolProposal]] = {}
    for proposal in tool_proposals:
        tools_by_server.setdefault(proposal.server, []).append(proposal)

    servers_in_catalog: dict[str, set[str]] = {}
    for tool in catalog_tools:
        cname = str(tool.get("name") or "")
        parts = split_catalog_tool_name(cname)
        if parts is None:
            continue
        srv, tname = parts
        if _tool_enabled(srv, tname, effective_deny):
            servers_in_catalog.setdefault(srv, set()).add(tname)

    rollup_servers: set[str] = set()
    for server, server_proposals in sorted(tools_by_server.items()):
        enabled_tools = servers_in_catalog.get(server, set())
        if not enabled_tools:
            leftover_tools.extend(server_proposals)
            continue
        proposed_tools = {proposal.tool for proposal in server_proposals}
        if proposed_tools >= enabled_tools and len(server_proposals) == len(enabled_tools):
            server_rollups.append(
                ServerRollupProposal(
                    server=server,
                    tools=tuple(server_proposals),
                    tier=target_tier,
                ),
            )
            rollup_servers.add(server)
        else:
            leftover_tools.extend(server_proposals)

    for proposal in tool_proposals:
        if proposal.server not in rollup_servers and proposal not in leftover_tools:
            leftover_tools.append(proposal)
    return server_rollups, leftover_tools


def _index_skill_proposals_by_root(
    skill_proposals: list[SkillProposal],
    skill_roots: list[Path],
) -> tuple[dict[Path, list[SkillProposal]], list[SkillProposal]]:
    skills_by_root: dict[Path, list[SkillProposal]] = {}
    unmatched: list[SkillProposal] = []
    for proposal in skill_proposals:
        skill_path = proposal.path
        if skill_path.name.lower() == "skill.md":
            skill_path = skill_path.parent
        root = _resolve_skill_root(skill_path, skill_roots)
        if root is None:
            unmatched.append(proposal)
            continue
        skills_by_root.setdefault(root, []).append(proposal)
    return skills_by_root, unmatched


def _partition_skill_proposals(
    skill_proposals: list[SkillProposal],
    *,
    enabled_skills: list[Any],
    skill_roots: list[Path],
    project_root: Path,
    target_tier: ProposalTier,
) -> tuple[list[SkillDirRollupProposal], list[SkillProposal]]:
    skill_dir_rollups: list[SkillDirRollupProposal] = []
    skills_by_root, leftover_skills = _index_skill_proposals_by_root(skill_proposals, skill_roots)

    enabled_skills_by_root: dict[Path, set[str]] = {}
    for item in enabled_skills:
        root = _resolve_skill_root(Path(item.path), skill_roots)
        if root is None:
            continue
        enabled_skills_by_root.setdefault(root, set()).add(item.name)

    rollup_dirs: set[Path] = set()
    for root, root_skill_proposals in sorted(skills_by_root.items(), key=lambda row: str(row[0])):
        enabled_names = enabled_skills_by_root.get(root, set())
        if not enabled_names:
            leftover_skills.extend(root_skill_proposals)
            continue
        proposed_names = {proposal.name for proposal in root_skill_proposals}
        if proposed_names >= enabled_names and len(root_skill_proposals) == len(enabled_names):
            skill_dir_rollups.append(
                SkillDirRollupProposal(
                    directory=root,
                    directory_display=_skill_dir_display(root, project_root),
                    skills=tuple(root_skill_proposals),
                    tier=target_tier,
                ),
            )
            rollup_dirs.add(root)
        else:
            leftover_skills.extend(root_skill_proposals)

    for proposal in skill_proposals:
        skill_path = (
            proposal.path.parent if proposal.path.name.lower() == "skill.md" else proposal.path
        )
        root = _resolve_skill_root(skill_path, skill_roots)
        if root not in rollup_dirs and proposal not in leftover_skills:
            leftover_skills.append(proposal)
    return skill_dir_rollups, leftover_skills


def build_tier_proposals(
    *,
    config: dict[str, Any],
    workspace_root: Path,
    agent: str,
    target_tier: ProposalTier,
) -> TierProposalBundle:
    from cyt.config import load_config
    from cyt.hook.workspace_config import resolve_hook_request_config
    from cyt.permissions.inventory.mcp import fetch_catalog_tools_sync
    from cyt.permissions.inventory.skills import list_skills
    from cyt.permissions.merge import effective_permissions
    from cyt.skills.directories import resolve_skill_directories
    from cyt.tiers.config import (
        resolve_tier_project,
        resolve_tier_status_agent,
        tier_section_config,
    )
    from cyt.tiers.manager import get_tier_manager
    from cyt.tiers.status_overview import build_status_overview

    base_config = config if config else load_config()
    project_root = resolve_tier_project(workspace=workspace_root)
    if project_root is None:
        return TierProposalBundle(tier=target_tier)
    status_agent = resolve_tier_status_agent(
        base_config,
        workspace_root=project_root,
        explicit=agent,
    )
    scoped_config, _ws = resolve_hook_request_config(
        {"workspace_root": str(project_root)},
        status_agent,
        base_config=base_config,
    )
    tool_cfg = tier_section_config(scoped_config, kind="tool")
    min_injections = tool_cfg.min_injections_before_reconsider
    wizard_cfg = wizard_config_from_dict(scoped_config)
    now_ms = int(time.time() * 1000)

    manager = get_tier_manager(scoped_config, workspace=project_root)
    status = manager.status(scoped_config, agent=status_agent)
    status_payload = {
        **status,
        "root_path": status.get("root_path") or str(project_root),
        "agent": status.get("agent") or status_agent,
    }
    status_payload["overview"] = build_status_overview(
        status_payload,
        config=scoped_config,
        workspace_root=project_root,
        agent=status_agent,
    )

    effective = effective_permissions(agent="all", workspace_root=project_root)
    catalog_tools = fetch_catalog_tools_sync(
        agent=status_agent,
        scope="effective",
        workspace_root=project_root,
        for_permissions_inventory=True,
    )

    tool_entities = _collect_entities_for_tier(
        _tier_detail_dict(status_payload, "tools"),
        target_tier=target_tier,
        wizard_cfg=wizard_cfg,
        min_injections=min_injections,
        now_ms=now_ms,
    )
    skill_entities = _collect_entities_for_tier(
        _tier_detail_dict(status_payload, "skills"),
        target_tier=target_tier,
        wizard_cfg=wizard_cfg,
        min_injections=min_injections,
        now_ms=now_ms,
    )

    tool_proposals = _build_tool_proposals(
        tool_entities=tool_entities,
        catalog_tools=catalog_tools,
        scoped_config=scoped_config,
        project_root=project_root,
        status_agent=status_agent,
        target_tier=target_tier,
        effective_deny=effective.mcp.deny,
    )

    enabled_skills, _disabled_skills = list_skills(
        agent="all",
        workspace_root=project_root,
    )
    skill_roots = resolve_skill_directories(
        scoped_config,
        agent=status_agent,
        workspace_root=project_root,
        include_platform_defaults=True,
    )
    skill_proposals = _build_skill_proposals(
        skill_entities=skill_entities,
        enabled_skills=enabled_skills,
        project_root=project_root,
        status_agent=status_agent,
        target_tier=target_tier,
        effective_deny=effective.skills.deny,
    )
    server_rollups, leftover_tools = _partition_tool_proposals(
        tool_proposals,
        catalog_tools=catalog_tools,
        effective_deny=effective.mcp.deny,
        target_tier=target_tier,
    )
    skill_dir_rollups, leftover_skills = _partition_skill_proposals(
        skill_proposals,
        enabled_skills=enabled_skills,
        skill_roots=skill_roots,
        project_root=project_root,
        target_tier=target_tier,
    )

    return TierProposalBundle(
        tier=target_tier,
        tools=tool_proposals,
        skills=skill_proposals,
        server_rollups=server_rollups,
        skill_dir_rollups=skill_dir_rollups,
        leftover_tools=leftover_tools,
        leftover_skills=leftover_skills,
    )


def _tool_enabled(server: str, tool: str, deny_entries: tuple[str, ...]) -> bool:
    from cyt.permissions.match import is_mcp_server_denied, is_mcp_tool_denied

    if is_mcp_server_denied(server, deny_entries):
        return False
    return not is_mcp_tool_denied(server, tool, deny_entries)


def _wizard_skill_tier_skip_notes(
    tier_items: list[Any],
    *,
    target_tier: ProposalTier,
    wizard_cfg: WizardConfig,
    min_injections: int,
    now_ms: int,
    effective_deny: tuple[str, ...],
    project_root: Path,
) -> tuple[list[str], list[str]]:
    from cyt.permissions.match import is_skill_permission_denied

    skipped_denied: list[str] = []
    skipped_recent: list[str] = []
    for entity in tier_items:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("display_name") or entity.get("name") or "?")
        source_path = entity.get("source_path") or entity.get("entity_id")
        path = Path(str(source_path)).expanduser() if source_path else None
        if path and is_skill_permission_denied(
            skill_name=name,
            skill_path=path,
            deny_entries=effective_deny,
            base=project_root,
        ):
            skipped_denied.append(name)
            continue
        if not _entity_eligible(
            entity,
            target_tier=target_tier,
            wizard_cfg=wizard_cfg,
            min_injections=min_injections,
            now_ms=now_ms,
        ):
            stats_raw = entity.get("stats")
            stats = stats_raw if isinstance(stats_raw, dict) else {}
            last_seen = int(stats.get("last_seen_ms") or 0)
            if last_seen > 0 and now_ms - last_seen < wizard_cfg.idle_ms:
                skipped_recent.append(name)
    return skipped_denied, skipped_recent


def _wizard_tool_tier_skip_notes(
    tier_items: list[Any],
    *,
    target_tier: ProposalTier,
    wizard_cfg: WizardConfig,
    min_injections: int,
    now_ms: int,
    effective_deny: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    from cyt.tiers.entity_origin import parse_tool_entity_id
    from cyt.tiers.status_detail import _is_tool_entity_denied

    skipped_denied: list[str] = []
    skipped_recent: list[str] = []
    for entity in tier_items:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id") or "").strip()
        _source, bare_name = parse_tool_entity_id(entity_id)
        label = str(entity.get("display_name") or entity.get("name") or bare_name or "?")
        if _is_tool_entity_denied(entity, effective_deny):
            skipped_denied.append(label)
            continue
        if not _entity_eligible(
            entity,
            target_tier=target_tier,
            wizard_cfg=wizard_cfg,
            min_injections=min_injections,
            now_ms=now_ms,
        ):
            stats_raw = entity.get("stats")
            stats = stats_raw if isinstance(stats_raw, dict) else {}
            last_seen = int(stats.get("last_seen_ms") or 0)
            if last_seen > 0 and now_ms - last_seen < wizard_cfg.idle_ms:
                skipped_recent.append(label)
    return skipped_denied, skipped_recent


def build_wizard_tier_notes(
    *,
    config: dict[str, Any],
    workspace_root: Path,
    agent: str,
    proposals: dict[ProposalTier, TierProposalBundle],
) -> dict[ProposalTier, str]:
    """Human-readable gap notes between tier stats and wizard proposals."""
    from cyt.config import load_config
    from cyt.hook.workspace_config import resolve_hook_request_config
    from cyt.permissions.merge import effective_permissions
    from cyt.tiers.config import (
        resolve_tier_project,
        resolve_tier_status_agent,
        tier_section_config,
    )
    from cyt.tiers.manager import get_tier_manager

    base_config = config if config else load_config()
    project_root = resolve_tier_project(workspace=workspace_root)
    if project_root is None:
        return {}

    status_agent = resolve_tier_status_agent(
        base_config,
        workspace_root=project_root,
        explicit=agent,
    )
    scoped_config, _ws = resolve_hook_request_config(
        {"workspace_root": str(project_root)},
        status_agent,
        base_config=base_config,
    )
    tool_cfg = tier_section_config(scoped_config, kind="tool")
    wizard_cfg = wizard_config_from_dict(scoped_config)
    now_ms = int(time.time() * 1000)
    effective = effective_permissions(agent="all", workspace_root=project_root)

    manager = get_tier_manager(scoped_config, workspace=project_root)
    status = manager.status(
        scoped_config,
        agent=status_agent,
        filter_by_permissions=False,
    )
    notes: dict[ProposalTier, str] = {}

    for target_tier in ("T0", "T1"):
        detail = _tier_detail_dict(status, "skills")
        by_tier = detail.get("by_tier") if isinstance(detail, dict) else {}
        tier_items = by_tier.get(target_tier) if isinstance(by_tier, dict) else []
        tier_count = len(tier_items) if isinstance(tier_items, list) else 0
        tool_detail = _tier_detail_dict(status, "tools")
        tool_by_tier = tool_detail.get("by_tier") if isinstance(tool_detail, dict) else {}
        tool_items = tool_by_tier.get(target_tier) if isinstance(tool_by_tier, dict) else []
        tool_count = len(tool_items) if isinstance(tool_items, list) else 0

        bundle = proposals[target_tier]
        proposed = len(bundle.tools) + len(bundle.skills)
        parts = [
            f"{tool_count} tool(s), {tier_count} skill(s) in tier stats",
            f"{proposed} to propose",
        ]

        if tier_count == 0 and tool_count == 0:
            notes[target_tier] = (
                f"no {target_tier} tools or skills in tier stats (tools may be T2+)"
            )
            continue

        skill_skipped_denied, skill_skipped_recent = _wizard_skill_tier_skip_notes(
            tier_items if isinstance(tier_items, list) else [],
            target_tier=target_tier,
            wizard_cfg=wizard_cfg,
            min_injections=tool_cfg.min_injections_before_reconsider,
            now_ms=now_ms,
            effective_deny=effective.skills.deny,
            project_root=project_root,
        )
        tool_skipped_denied, tool_skipped_recent = _wizard_tool_tier_skip_notes(
            tool_items if isinstance(tool_items, list) else [],
            target_tier=target_tier,
            wizard_cfg=wizard_cfg,
            min_injections=tool_cfg.min_injections_before_reconsider,
            now_ms=now_ms,
            effective_deny=effective.mcp.deny,
        )
        skipped_denied = [*tool_skipped_denied, *skill_skipped_denied]
        skipped_recent = [*tool_skipped_recent, *skill_skipped_recent]
        if skipped_denied:
            parts.append(f"already denied: {', '.join(skipped_denied)}")
        if skipped_recent:
            parts.append(f"recently active: {', '.join(skipped_recent)}")
        notes[target_tier] = "; ".join(parts)

    return notes


def build_all_proposals(
    *,
    config: dict[str, Any],
    workspace_root: Path,
    agent: str = "cursor",
) -> dict[ProposalTier, TierProposalBundle]:
    return {
        "T0": build_tier_proposals(
            config=config,
            workspace_root=workspace_root,
            agent=agent,
            target_tier="T0",
        ),
        "T1": build_tier_proposals(
            config=config,
            workspace_root=workspace_root,
            agent=agent,
            target_tier="T1",
        ),
    }


def bundle_summary(bundle: TierProposalBundle) -> dict[str, Any]:
    return {
        "tier": bundle.tier,
        "tools": len(bundle.tools),
        "skills": len(bundle.skills),
        "server_rollups": len(bundle.server_rollups),
        "skill_dir_rollups": len(bundle.skill_dir_rollups),
    }


def format_tool_proposal_display(
    proposal: ToolProposal,
    *,
    workspace_root: Path | None = None,
) -> str:
    line = f"tool {proposal.server}/{proposal.tool}"
    if proposal.server_config_path:
        config_display = _format_wizard_path(
            proposal.server_config_path,
            workspace_root=workspace_root,
        )
        if proposal.server_config_line:
            return f"{line}  ({config_display}:L{proposal.server_config_line})"
        return f"{line}  ({config_display})"
    return line


def format_skill_proposal_display(
    proposal: SkillProposal,
    *,
    workspace_root: Path | None = None,
) -> str:
    line = f"skill {proposal.name}"
    if proposal.discovery_directory:
        directory_display = _format_wizard_path(
            proposal.discovery_directory,
            workspace_root=workspace_root,
        )
        if proposal.discovery_config_path:
            config_display = _format_wizard_path(
                proposal.discovery_config_path,
                workspace_root=workspace_root,
            )
            return f"{line}  ({config_display}: {directory_display})"
        return f"{line}  ({directory_display})"
    return line


def _format_wizard_path(
    path: str | Path | None,
    *,
    workspace_root: Path | None = None,
) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return text

    path_obj = Path(text)
    if not path_obj.is_absolute() and not text.startswith("~"):
        return text
    if text.startswith("~/"):
        return text

    from cyt.tiers.status_overview import _short_path

    expanded = path_obj.expanduser()
    if workspace_root is not None:
        try:
            resolved = expanded.resolve()
            ws = workspace_root.expanduser().resolve()
            rel = resolved.relative_to(ws)
            return "." if str(rel) == "." else str(rel)
        except (OSError, ValueError):
            pass
    return _short_path(expanded)
