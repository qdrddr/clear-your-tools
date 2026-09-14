"""Integration tests for ``cyt inject preview`` end-to-end catalog loading."""

from __future__ import annotations

import pytest

from cyt.tools.inject_cli import main as inject_main
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
    InjectPreviewScenario,
    disk_catalog_inject_preview_pack,
    json_payload_from_stdout,
    load_inject_preview_scenarios,
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
