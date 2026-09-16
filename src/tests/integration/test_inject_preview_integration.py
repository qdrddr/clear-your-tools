"""Integration tests for ``cyt inject preview`` end-to-end catalog loading."""

from __future__ import annotations

import pytest

from cyt.tools.inject_cli import main as inject_main
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
    InjectPreviewScenario,
    json_payload_from_stdout,
    load_inject_preview_scenarios,
    patch_preview_prune_all_tools,
    tool_log_entry,
    write_session_log,
)


@pytest.mark.parametrize(
    "scenario",
    load_inject_preview_scenarios(),
    ids=[item.id for item in load_inject_preview_scenarios()],
)
def test_inject_preview_cli_json_output_from_workspace_disk_catalog(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: InjectPreviewScenario,
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            scenario.query,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
            "--json",
        ],
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json_payload_from_stdout(captured.out)
    assert payload["workspace"] == str(pack.workspace.resolve())
    assert payload["prune"]["cyt_mcp"]["tools_in"] == scenario.expected_catalog_tool_count
    pruned_names = {tool["name"] for tool in payload["tools"]["cyt_mcp"]}
    assert pruned_names.issubset(set(scenario.expected_pruned_tool_names))
    assert pruned_names, "expected at least one pruned tool"
    for marker in scenario.expected_injection_markers:
        assert marker in payload["injection"]
    for name in pruned_names:
        assert f"name='{name}'" in payload["injection"]


def test_inject_preview_cli_prints_agent_tools_xml(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            pack.scenario.query,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
        ],
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "<agent-tools" in captured.out
    assert "<cyt-mcp>" in captured.out
    assert "No tools in master hook catalog" not in captured.err


def test_inject_preview_cli_session_skips_previously_injected_tool(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)
    patch_preview_prune_all_tools(monkeypatch)

    fff_tool = next(tool for tool in pack.tools if tool["name"] == "fff_grep")
    session_id = "integration-post-compaction"
    write_session_log(
        pack.workspace,
        session_id,
        [
            {"kind": "compaction", "key": "compaction", "payload": {}},
            tool_log_entry(fff_tool, catalog_tools=pack.tools, full=True),
        ],
    )

    code = inject_main(
        [
            "preview",
            pack.scenario.query,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
            "--session",
            session_id,
            "--json",
        ],
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json_payload_from_stdout(captured.out)
    assert payload["session_id"] == session_id
    assert str(pack.workspace) in payload["session_log_path"]
    assert "fff_grep" in payload["session_gate"]["skipped_tools"]["cyt_mcp"]
    assert "name='fff_grep'" not in payload["injection"]


def test_inject_preview_cli_missing_session_exits_nonzero(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            pack.scenario.query,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
            "--session",
            "missing-integration-session",
        ],
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "Session log not found:" in captured.err
