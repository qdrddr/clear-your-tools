"""Interactive permissions wizard driven by tier statistics."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cyt.permissions.proposals import (
    ProposalTier,
    SkillProposal,
    TierProposalBundle,
    build_all_proposals,
    build_wizard_tier_notes,
    bundle_summary,
    format_skill_proposal_display,
    format_tool_proposal_display,
)


class WizardInterruptedError(Exception):
    """User cancelled the permissions wizard (Ctrl+C)."""


WizardInterrupted = WizardInterruptedError


@dataclass
class AppliedChanges:
    servers: list[str] = field(default_factory=list)
    tools: list[tuple[str, str]] = field(default_factory=list)
    skill_dirs: list[Path] = field(default_factory=list)
    skill_files: list[SkillProposal] = field(default_factory=list)

    @property
    def any_changes(self) -> bool:
        return bool(self.servers or self.tools or self.skill_dirs or self.skill_files)


def _prompt_yes_no(text: str, *, default: bool) -> bool:
    from cyt.proxy.setup_wizard import _prompt_yes_no as wizard_prompt

    try:
        return wizard_prompt(text, default_yes=default)
    except KeyboardInterrupt:
        raise WizardInterruptedError from None


def _print_wizard_overview(
    proposals: dict[ProposalTier, TierProposalBundle],
    *,
    tier_notes: dict[ProposalTier, str] | None = None,
) -> None:
    for tier in ("T0", "T1"):
        bundle = proposals[tier]
        note = (tier_notes or {}).get(tier, "")
        if note:
            print(f"  {tier}: {note}")
            continue
        tool_count = len(bundle.tools)
        skill_count = len(bundle.skills)
        if tool_count or skill_count:
            print(
                f"  {tier}: {tool_count} tool(s), {skill_count} skill(s) eligible to disable",
            )
        else:
            print(f"  {tier}: nothing to propose")


def _print_bundle_proposals(
    bundle: TierProposalBundle,
    *,
    workspace_root: Path | None = None,
) -> None:
    if not bundle.tools and not bundle.skills:
        return
    print(f"\nProposed {bundle.tier} disables:")
    for tool_proposal in bundle.tools:
        print(f"  {format_tool_proposal_display(tool_proposal, workspace_root=workspace_root)}")
    for skill_proposal in bundle.skills:
        print(f"  {format_skill_proposal_display(skill_proposal, workspace_root=workspace_root)}")


def _skill_directory_for_proposal(proposal: SkillProposal) -> Path:
    if proposal.path.name.lower() == "skill.md":
        return proposal.path.parent
    return proposal.path


def _apply_accept_all_decisions(bundle: TierProposalBundle) -> AppliedChanges:
    changes = AppliedChanges()
    for server_rollup in bundle.server_rollups:
        changes.servers.append(server_rollup.server)
    for skill_dir_rollup in bundle.skill_dir_rollups:
        changes.skill_dirs.append(skill_dir_rollup.directory)
    rollup_servers = {rollup.server for rollup in bundle.server_rollups}
    rollup_dirs = {rollup.directory for rollup in bundle.skill_dir_rollups}
    for tool_proposal in bundle.tools:
        if tool_proposal.server not in rollup_servers:
            changes.tools.append((tool_proposal.server, tool_proposal.tool))
    for skill_proposal in bundle.skills:
        skill_dir = _skill_directory_for_proposal(skill_proposal)
        if skill_dir not in rollup_dirs:
            changes.skill_files.append(skill_proposal)
    return changes


def _prompt_server_rollups(
    bundle: TierProposalBundle,
    changes: AppliedChanges,
    *,
    default_yes: bool,
    auto_yes: bool,
    interactive: bool,
) -> None:
    for server_rollup in bundle.server_rollups:
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable entire MCP server {server_rollup.server!r} "
                f"({len(server_rollup.tools)} tools)?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.servers.append(server_rollup.server)


def _prompt_skill_dir_rollups(
    bundle: TierProposalBundle,
    changes: AppliedChanges,
    *,
    default_yes: bool,
    auto_yes: bool,
    interactive: bool,
) -> None:
    for skill_dir_rollup in bundle.skill_dir_rollups:
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable entire skill directory {skill_dir_rollup.directory_display!r} "
                f"({len(skill_dir_rollup.skills)} skills)?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.skill_dirs.append(skill_dir_rollup.directory)


def _prompt_leftover_tools(
    bundle: TierProposalBundle,
    changes: AppliedChanges,
    *,
    default_yes: bool,
    auto_yes: bool,
    interactive: bool,
) -> None:
    accepted_servers = set(changes.servers)
    for tool_proposal in bundle.leftover_tools:
        if tool_proposal.server in accepted_servers:
            continue
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable tool {tool_proposal.server}/{tool_proposal.tool}?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.tools.append((tool_proposal.server, tool_proposal.tool))


def _prompt_leftover_skills(
    bundle: TierProposalBundle,
    changes: AppliedChanges,
    *,
    default_yes: bool,
    auto_yes: bool,
    interactive: bool,
) -> None:
    accepted_dirs = set(changes.skill_dirs)
    for skill_proposal in bundle.leftover_skills:
        skill_dir = _skill_directory_for_proposal(skill_proposal)
        if skill_dir in accepted_dirs:
            continue
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable skill {skill_proposal.name!r} ({skill_proposal.path})?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.skill_files.append(skill_proposal)


def _collect_bundle_decisions(
    bundle: TierProposalBundle,
    *,
    default_yes: bool,
    auto_yes: bool,
    interactive: bool,
    workspace_root: Path | None = None,
) -> AppliedChanges:
    changes = AppliedChanges()
    if not bundle.tools and not bundle.skills:
        return changes

    total = len(bundle.tools) + len(bundle.skills)
    if auto_yes:
        accept_all = default_yes
    elif interactive:
        _print_bundle_proposals(bundle, workspace_root=workspace_root)
        accept_all = _prompt_yes_no(
            f"Disable all {total} proposed {bundle.tier} tools/skills?",
            default=default_yes,
        )
    else:
        return changes

    if accept_all:
        return _apply_accept_all_decisions(bundle)

    _prompt_server_rollups(
        bundle,
        changes,
        default_yes=default_yes,
        auto_yes=auto_yes,
        interactive=interactive,
    )
    _prompt_skill_dir_rollups(
        bundle,
        changes,
        default_yes=default_yes,
        auto_yes=auto_yes,
        interactive=interactive,
    )
    _prompt_leftover_tools(
        bundle,
        changes,
        default_yes=default_yes,
        auto_yes=auto_yes,
        interactive=interactive,
    )
    _prompt_leftover_skills(
        bundle,
        changes,
        default_yes=default_yes,
        auto_yes=auto_yes,
        interactive=interactive,
    )
    return changes


def apply_changes(
    changes: AppliedChanges,
    *,
    workspace_root: Path,
    agent_target: str = "all",
) -> None:
    from cyt.permissions.editor import disable_mcp_server, disable_mcp_tool, disable_skill
    from cyt.permissions.paths import normalize_agent_target

    target = normalize_agent_target(agent_target)
    accepted_servers = set(changes.servers)
    accepted_dirs = set(changes.skill_dirs)

    for server in changes.servers:
        disable_mcp_server(
            server,
            scope="workspace",
            agent_target=target,
            agent=agent_target,
            workspace_root=workspace_root,
        )

    for server, tool in changes.tools:
        if server in accepted_servers:
            continue
        disable_mcp_tool(
            server,
            tool,
            scope="workspace",
            agent_target=target,
            agent=agent_target,
            workspace_root=workspace_root,
        )

    for directory in changes.skill_dirs:
        disable_skill(
            "",
            scope="workspace",
            agent_target=target,
            agent=agent_target,
            workspace_root=workspace_root,
            skill_path=directory,
        )

    for proposal in changes.skill_files:
        skill_dir = _skill_directory_for_proposal(proposal)
        if skill_dir in accepted_dirs:
            continue
        disable_skill(
            proposal.name,
            scope="workspace",
            agent_target=target,
            agent=agent_target,
            workspace_root=workspace_root,
            skill_path=skill_dir,
        )


def run_permissions_wizard(args: argparse.Namespace) -> int:
    from cyt.permissions.cli import _resolved_notify_workspace
    from cyt.proxy.transport import INTERRUPTED_EXIT_CODE

    workspace = _resolved_notify_workspace(args)
    if workspace is None:
        print("No workspace detected; run from a git project root.", file=sys.stderr)
        return 2

    if getattr(args, "no_wizard", False):
        print(
            "Tier wizard skipped (--no-wizard). Use subcommands: show, export, mcp, skills.",
            file=sys.stderr,
        )
        return 2

    try:
        return _run_permissions_wizard_body(args, workspace)
    except WizardInterruptedError:
        print("\nWizard cancelled.", file=sys.stderr)
        return INTERRUPTED_EXIT_CODE


def _run_permissions_wizard_body(args: argparse.Namespace, workspace: Path) -> int:
    from cyt.config import load_config
    from cyt.permissions.cli import _notify_after_permissions_write
    from cyt.permissions.paths import resolve_inventory_agent

    config = load_config()
    agent = resolve_inventory_agent(getattr(args, "agent", "all"))
    dry_run = bool(getattr(args, "dry_run", False))
    auto_yes = bool(getattr(args, "yes", False))
    interactive = sys.stdin.isatty()

    proposals = build_all_proposals(config=config, workspace_root=workspace, agent=agent)
    tier_notes = build_wizard_tier_notes(
        config=config,
        workspace_root=workspace,
        agent=agent,
        proposals=proposals,
    )
    if not interactive and not auto_yes:
        summary = {tier: bundle_summary(bundle) for tier, bundle in proposals.items()}
        print(
            json.dumps(
                {"workspace": str(workspace), "proposals": summary, "notes": tier_notes},
                indent=2,
            ),
        )
        print("Run interactively or pass --yes to apply T0 defaults.", file=sys.stderr)
        return 0

    print(f"CYT permissions wizard (workspace={workspace})")
    _print_wizard_overview(proposals, tier_notes=tier_notes)

    all_changes = AppliedChanges()
    t0 = _collect_bundle_decisions(
        proposals["T0"],
        default_yes=True,
        auto_yes=auto_yes,
        interactive=interactive,
        workspace_root=workspace,
    )
    t1 = _collect_bundle_decisions(
        proposals["T1"],
        default_yes=False,
        auto_yes=False,
        interactive=interactive and not auto_yes,
        workspace_root=workspace,
    )

    all_changes.servers.extend(t0.servers + t1.servers)
    all_changes.tools.extend(t0.tools + t1.tools)
    all_changes.skill_dirs.extend(t0.skill_dirs + t1.skill_dirs)
    all_changes.skill_files.extend(t0.skill_files + t1.skill_files)

    if dry_run:
        print("Dry run — no changes written.")
        print(
            json.dumps(
                {
                    "servers": all_changes.servers,
                    "tools": all_changes.tools,
                    "skill_dirs": [str(path) for path in all_changes.skill_dirs],
                    "skills": [
                        {"name": item.name, "path": str(item.path)}
                        for item in all_changes.skill_files
                    ],
                },
                indent=2,
            ),
        )
        return 0

    apply_changes(
        all_changes,
        workspace_root=workspace,
        agent_target=str(getattr(args, "agent", "all") or "all").strip().lower() or "all",
    )
    if all_changes.any_changes:
        print(
            f"Applied: {len(all_changes.servers)} servers, "
            f"{len(all_changes.tools)} tools, "
            f"{len(all_changes.skill_dirs)} skill directories, "
            f"{len(all_changes.skill_files)} skills.",
        )
        _notify_after_permissions_write(args)
    else:
        print("No changes applied.")
    return 0
