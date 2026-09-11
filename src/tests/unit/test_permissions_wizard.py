"""Tests for permissions wizard helpers."""

from __future__ import annotations

from pathlib import Path

from cyt.permissions.proposals import SkillProposal, TierProposalBundle, ToolProposal
from cyt.permissions.wizard import (
    AppliedChanges,
    WizardInterrupted,
    _collect_bundle_decisions,
    _print_bundle_proposals,
    apply_changes,
    run_permissions_wizard,
)


def test_collect_bundle_decisions_auto_yes_t0_defaults() -> None:
    bundle = TierProposalBundle(
        tier="T0",
        tools=[
            ToolProposal(server="srv", tool="alpha", entity_id="x", tier="T0"),
        ],
        skills=[],
        server_rollups=[],
        skill_dir_rollups=[],
        leftover_tools=[
            ToolProposal(server="srv", tool="alpha", entity_id="x", tier="T0"),
        ],
        leftover_skills=[],
    )
    changes = _collect_bundle_decisions(
        bundle,
        default_yes=True,
        auto_yes=True,
        interactive=False,
    )
    assert ("srv", "alpha") in changes.tools


def test_apply_changes_writes_workspace_overlay(tmp_path: Path) -> None:
    ws_cfg = tmp_path / ".agents" / "cyt" / "config"
    ws_cfg.mkdir(parents=True)
    (ws_cfg / "config.yaml").write_text("tools:\n  permissions:\n    deny: []\n", encoding="utf-8")

    changes = AppliedChanges(
        servers=["demo-server"],
        tools=[],
        skill_dirs=[],
        skill_files=[
            SkillProposal(
                name="demo", path=tmp_path / "skill" / "SKILL.md", entity_id="", tier="T0"
            ),
        ],
    )
    apply_changes(changes, workspace_root=tmp_path, agent_target="all")

    import yaml

    raw = yaml.safe_load((ws_cfg / "config.yaml").read_text(encoding="utf-8"))
    assert "demo-server" in raw["tools"]["permissions"]["deny"]


def test_run_permissions_wizard_no_wizard_flag_exits(tmp_path: Path) -> None:
    import argparse

    args = argparse.Namespace(
        no_wizard=True,
        workspace=tmp_path,
        agent="all",
        yes=False,
        dry_run=False,
        config=None,
        scope="workspace",
        json=False,
    )
    assert run_permissions_wizard(args) == 2


def test_print_bundle_proposals_lists_tools_and_skills(capsys) -> None:
    bundle = TierProposalBundle(
        tier="T1",
        tools=[
            ToolProposal(
                server="srv",
                tool="alpha",
                entity_id="x",
                tier="T1",
                server_config_path="/tmp/mcp/cursor.json",
                server_config_line=12,
            ),
        ],
        skills=[
            SkillProposal(
                name="explain-simply",
                path=Path("/tmp/skill/SKILL.md"),
                entity_id="y",
                tier="T1",
                discovery_config_path="/tmp/config.yaml",
                discovery_directory=".agents/skills",
            ),
        ],
    )
    _print_bundle_proposals(bundle, workspace_root=Path("/tmp"))
    captured = capsys.readouterr()
    assert "Proposed T1 disables:" in captured.out
    assert "tool srv/alpha  (mcp/cursor.json:L12)" in captured.out
    assert "skill explain-simply  (config.yaml: .agents/skills)" in captured.out


def test_collect_bundle_decisions_interrupt_exits_cleanly(monkeypatch) -> None:
    bundle = TierProposalBundle(
        tier="T1",
        skills=[
            SkillProposal(name="demo", path=Path("/tmp/skill/SKILL.md"), entity_id="x", tier="T1"),
        ],
    )

    def raise_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("cyt.proxy.setup_wizard._prompt_yes_no", raise_interrupt)
    try:
        _collect_bundle_decisions(
            bundle,
            default_yes=False,
            auto_yes=False,
            interactive=True,
        )
        raise AssertionError("expected WizardInterrupted")
    except WizardInterrupted:
        pass


def test_run_permissions_wizard_interrupt_returns_130(monkeypatch, tmp_path: Path) -> None:
    import argparse

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cyt.permissions.wizard._collect_bundle_decisions",
        lambda *args, **kwargs: (_ for _ in ()).throw(WizardInterrupted()),
    )
    monkeypatch.setattr(
        "cyt.permissions.wizard.build_all_proposals",
        lambda **kwargs: {"T0": TierProposalBundle(tier="T0"), "T1": TierProposalBundle(tier="T1")},
    )

    args = argparse.Namespace(
        no_wizard=False,
        workspace=tmp_path,
        agent="all",
        yes=False,
        dry_run=False,
        config=None,
        scope="workspace",
        json=False,
    )
    assert run_permissions_wizard(args) == 130
