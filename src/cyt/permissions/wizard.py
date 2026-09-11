"""Interactive permissions wizard driven by tier statistics."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyt.permissions.proposals import (
    SkillProposal,
    TierProposalBundle,
    build_all_proposals,
    bundle_summary,
    format_skill_proposal_display,
    format_tool_proposal_display,
)


class WizardInterrupted(Exception):
    """User cancelled the permissions wizard (Ctrl+C)."""


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
        raise WizardInterrupted from None


def _print_bundle_proposals(
    bundle: TierProposalBundle, *, workspace_root: Path | None = None
) -> None:
    if not bundle.tools and not bundle.skills:
        return
    print(f"\nProposed {bundle.tier} disables:")
    for proposal in bundle.tools:
        print(f"  {format_tool_proposal_display(proposal, workspace_root=workspace_root)}")
    for proposal in bundle.skills:
        print(f"  {format_skill_proposal_display(proposal, workspace_root=workspace_root)}")


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
        for rollup in bundle.server_rollups:
            changes.servers.append(rollup.server)
        for rollup in bundle.skill_dir_rollups:
            changes.skill_dirs.append(rollup.directory)
        rollup_servers = {rollup.server for rollup in bundle.server_rollups}
        rollup_dirs = {rollup.directory for rollup in bundle.skill_dir_rollups}
        for proposal in bundle.tools:
            if proposal.server not in rollup_servers:
                changes.tools.append((proposal.server, proposal.tool))
        for proposal in bundle.skills:
            skill_dir = (
                proposal.path.parent if proposal.path.name.lower() == "skill.md" else proposal.path
            )
            if skill_dir not in rollup_dirs:
                changes.skill_files.append(proposal)
        return changes

    for rollup in bundle.server_rollups:
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable entire MCP server {rollup.server!r} ({len(rollup.tools)} tools)?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.servers.append(rollup.server)

    for rollup in bundle.skill_dir_rollups:
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable entire skill directory {rollup.directory_display!r} "
                f"({len(rollup.skills)} skills)?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.skill_dirs.append(rollup.directory)

    accepted_servers = set(changes.servers)
    accepted_dirs = set(changes.skill_dirs)

    for proposal in bundle.leftover_tools:
        if proposal.server in accepted_servers:
            continue
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable tool {proposal.server}/{proposal.tool}?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.tools.append((proposal.server, proposal.tool))

    for proposal in bundle.leftover_skills:
        skill_dir = (
            proposal.path.parent if proposal.path.name.lower() == "skill.md" else proposal.path
        )
        if skill_dir in accepted_dirs:
            continue
        if auto_yes:
            accept = default_yes
        elif interactive:
            accept = _prompt_yes_no(
                f"Disable skill {proposal.name!r} ({proposal.path})?",
                default=default_yes,
            )
        else:
            continue
        if accept:
            changes.skill_files.append(proposal)

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
        skill_dir = (
            proposal.path.parent if proposal.path.name.lower() == "skill.md" else proposal.path
        )
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


def run_permissions_wizard(args: Any) -> int:
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
    except WizardInterrupted:
        print("\nWizard cancelled.", file=sys.stderr)
        return INTERRUPTED_EXIT_CODE


def _run_permissions_wizard_body(args: Any, workspace: Path) -> int:
    from cyt.config import load_config
    from cyt.permissions.cli import _notify_after_permissions_write
    from cyt.permissions.paths import resolve_inventory_agent

    config = load_config()
    agent = resolve_inventory_agent(getattr(args, "agent", "all"))
    dry_run = bool(getattr(args, "dry_run", False))
    auto_yes = bool(getattr(args, "yes", False))
    interactive = sys.stdin.isatty()

    proposals = build_all_proposals(config=config, workspace_root=workspace, agent=agent)
    if not interactive and not auto_yes:
        summary = {tier: bundle_summary(bundle) for tier, bundle in proposals.items()}
        print(json.dumps({"workspace": str(workspace), "proposals": summary}, indent=2))
        print("Run interactively or pass --yes to apply T0 defaults.", file=sys.stderr)
        return 0

    print(f"CYT permissions wizard (workspace={workspace})")

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
