"""Integration tests for cyt_mcp session log wire name pre-exposure."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.config import load_config
from cyt.cyt_mcp.catalog import apply_fetched_catalog, clear_cyt_mcp_catalog_cache
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tools.inject_cli import main as inject_main
from cyt.tools.master_catalog import clear_master_catalog_cache, rebuild_master_catalog
from tests.support.cyt_mcp_session_log_wire_name_fixtures import (
    InjectPreviewExpectation,
    catalog_tool_by_wire_name,
    load_inject_preview_expectations,
    materialize_inject_preview_pack_with_wire_catalog,
    session_entry_for_source,
)
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
    json_payload_from_stdout,
    patch_inject_preview_environment,
    patch_preview_prune_all_tools,
    write_session_log,
)


@pytest.fixture
def wire_name_inject_preview_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[InjectPreviewFixturePack]:
    pack = materialize_inject_preview_pack_with_wire_catalog(tmp_path)
    patch_inject_preview_environment(monkeypatch, pack)
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    config = load_config(pack.global_config_path)
    scoped = set_hook_workspace_in_config(config, pack.workspace)
    apply_fetched_catalog(scoped, pack.tools)
    rebuild_master_catalog(scoped, blocking=True)
    yield pack
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()


@pytest.mark.parametrize(
    "expectation",
    load_inject_preview_expectations(),
    ids=lambda item: item.id,
)
def test_inject_preview_session_wire_name_gate_from_fixture(
    wire_name_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    expectation: InjectPreviewExpectation,
) -> None:
    pack = wire_name_inject_preview_pack
    monkeypatch.chdir(pack.workspace)
    patch_preview_prune_all_tools(monkeypatch)

    session_entry = session_entry_for_source(
        expectation.session_entry_source,
        wire_name=expectation.wire_name,
        catalog_tools=pack.tools,
    )
    session_id = f"wire-name-{expectation.id}"
    write_session_log(
        pack.workspace,
        session_id,
        [
            {"kind": "compaction", "key": "compaction", "payload": {}},
            session_entry,
        ],
    )

    code = inject_main(
        [
            "preview",
            "grep BM25 implementation",
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

    gate = payload["session_gate"]
    skipped = gate.get("skipped_tools", {}).get("cyt_mcp", [])
    injected = gate.get("injected_tools", {}).get("cyt_mcp", [])
    injection = payload["injection"]

    if expectation.session_entry_source == "wire_tool_log_entry":
        assert expectation.wire_name in skipped
        assert expectation.wire_name not in injected
        assert expectation.expect_injection_contains not in injection
    else:
        assert expectation.wire_name in injected
        assert expectation.wire_name not in skipped
        assert expectation.expect_injection_contains in injection


def test_wire_format_session_log_entry_matches_catalog_tool(
    wire_name_inject_preview_pack: InjectPreviewFixturePack,
) -> None:
    pack = wire_name_inject_preview_pack
    tool = catalog_tool_by_wire_name("fff_grep")
    entry = session_entry_for_source(
        "wire_tool_log_entry",
        wire_name="fff_grep",
        catalog_tools=pack.tools,
    )
    assert entry["name"] == tool["name"]
    assert entry["key"] == f"tool:cyt_mcp:{tool['name']}"
