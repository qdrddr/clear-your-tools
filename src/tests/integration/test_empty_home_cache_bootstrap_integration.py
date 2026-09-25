"""Integration tests for empty cyt home cache bootstrap via inject preview CLI."""

from __future__ import annotations

import pytest

from cyt.tools.inject_cli import main as inject_main
from tests.support.empty_home_cache_bootstrap_fixtures import (
    EmptyHomeFixturePack,
    EmptyHomeScenario,
    load_bootstrap_scenario,
    load_bootstrap_scenarios,
    simulate_cyt_mcp_push,
    simulate_daemon_warm,
)
from tests.support.inject_preview_fixtures import json_payload_from_stdout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    [load_bootstrap_scenario("cyt_mcp_push_then_inject_preview_ok")],
    ids=["cyt_mcp_push_then_inject_preview_ok"],
)
def test_inject_preview_cli_after_cyt_mcp_push(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: EmptyHomeScenario,
) -> None:
    pack = isolated_empty_home_pack
    simulate_cyt_mcp_push(pack)
    simulate_daemon_warm(pack)
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            pack.bm25_prompt,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
            "--json",
        ],
    )
    captured = capsys.readouterr()

    assert code == scenario.raw.get("expect_exit_code", 0), captured.err
    for fragment in scenario.raw.get("expect_stderr_excludes", []):
        assert fragment not in captured.err

    payload = json_payload_from_stdout(captured.out)
    assert payload["workspace"] == str(pack.workspace.resolve())
    pruned_names = {tool["name"] for tool in payload["tools"]["cyt_mcp"]}
    assert pruned_names.issubset(set(pack.expected_tool_names))
    assert pruned_names, "expected at least one pruned tool"
    for marker in pack.expected_injection_markers:
        assert marker in payload["injection"]


def test_inject_preview_cli_negative_empty_home(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_bootstrap_scenario("cold_home_inject_preview_fails")
    pack = isolated_empty_home_pack
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            pack.bm25_prompt,
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
        ],
    )
    captured = capsys.readouterr()

    assert code == scenario.raw.get("expect_exit_code", 1)
    for fragment in scenario.raw.get("expect_stderr_contains", []):
        assert fragment in captured.err


@pytest.mark.parametrize(
    "scenario_id",
    [item.id for item in load_bootstrap_scenarios()],
    ids=[item.id for item in load_bootstrap_scenarios()],
)
def test_bootstrap_scenarios_declared(
    scenario_id: str,
) -> None:
    scenario = load_bootstrap_scenario(scenario_id)
    assert scenario.description
