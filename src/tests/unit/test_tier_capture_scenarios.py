"""Unit tests for cyt-mcp tool-use tier capture using shared fixture files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams, TextContent

from cyt.hook.daemon_client import normalize_hook_base_url, resolve_hook_path
from cyt.tiers.config import (
    tier_section_config,
    tier_tool_capture_source,
    tier_tool_capture_via_hooks,
)
from cyt.tiers.manager import _managers
from cyt.tiers.models import EntityKind
from cyt.tiers.scores import execution_score, utility_score
from cyt.tiers.status_detail import build_kind_detail
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.tier_feedback_push import _build_payload
from cyt_mcp.tool_use_feedback_middleware import ToolUseFeedbackMiddleware, _is_meta_tool
from tests.support.tier_capture_fixtures import (
    AttemptSequenceScenario,
    HttpPayloadScenario,
    TierCaptureFixturePack,
    apply_attempt_sequence,
    capture_tool_dict,
    load_attempt_sequences,
    load_capture_config_values,
    load_capture_tool,
    load_http_payloads,
    load_meta_tools_not_reported,
    manager_for_pack,
    materialize_capture_pack,
)


@pytest.fixture
def capture_pack(tmp_path: Path) -> TierCaptureFixturePack:
    return materialize_capture_pack(tmp_path)


@pytest.fixture(autouse=True)
def clear_tier_managers_for_capture() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture(autouse=True)
def isolate_capture_master_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: None,
    )


def test_capture_fixture_scenarios_are_self_consistent() -> None:
    tool = load_capture_tool()
    sequences = load_attempt_sequences()
    payloads = load_http_payloads()
    meta_tools = load_meta_tools_not_reported()
    config_values = load_capture_config_values()

    assert tool.entity_id == f"cyt_mcp:{tool.wire_name}"
    assert len(sequences) == 3
    assert len(payloads) == 3
    assert len(meta_tools) == 3
    assert config_values["capture_default"] == "cyt_mcp"
    assert config_values["capture_hooks"] == "hooks"

    for sequence in sequences:
        assert sequence.steps
        assert "attempts" in sequence.expected
        assert "used" in sequence.expected


@pytest.mark.parametrize("scenario", load_attempt_sequences(), ids=lambda item: item.id)
def test_attempt_sequences_update_attempts_and_used(
    capture_pack: TierCaptureFixturePack,
    scenario: AttemptSequenceScenario,
) -> None:
    manager = manager_for_pack(capture_pack)
    try:
        apply_attempt_sequence(manager, capture_pack, scenario)
        state = manager._states.get((EntityKind.TOOL, capture_pack.tool.entity_id))
        assert state is not None
        assert state.stats.attempts == scenario.expected["attempts"]
        assert state.stats.used == scenario.expected["used"]
        if scenario.expected.get("execution_lt_utility"):
            assert execution_score(state.stats) < utility_score(state.stats)
    finally:
        manager.close()


@pytest.mark.parametrize("scenario", load_http_payloads(), ids=lambda item: item.id)
def test_record_tool_attempt_feedback_matches_http_payload_expectations(
    capture_pack: TierCaptureFixturePack,
    scenario: HttpPayloadScenario,
) -> None:
    from cyt.tiers.feedback import record_tool_attempt_feedback

    record_tool_attempt_feedback(
        tool_name=str(scenario.payload["tool_name"]),
        catalog=str(scenario.payload.get("catalog") or "cyt_mcp"),
        success=scenario.payload.get("success") is not False,
        config=capture_pack.config,
        args=scenario.payload.get("args")
        if isinstance(scenario.payload.get("args"), dict)
        else None,
        workspace=capture_pack.workspace,
        optional_used=scenario.payload.get("optional_used") is True,
    )

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(capture_pack.config, workspace=capture_pack.workspace)
    state = manager._states.get((EntityKind.TOOL, capture_pack.tool.entity_id))
    assert state is not None
    assert state.stats.attempts == scenario.expected["attempts"]
    assert state.stats.used == scenario.expected["used"]


def test_status_detail_includes_attempts_and_execution_score(
    capture_pack: TierCaptureFixturePack,
) -> None:
    scenario = next(
        item for item in load_attempt_sequences() if item.id == "mixed_two_fail_one_success"
    )
    manager = manager_for_pack(capture_pack)
    try:
        apply_attempt_sequence(manager, capture_pack, scenario)
        state = manager._states[(EntityKind.TOOL, capture_pack.tool.entity_id)]
    finally:
        manager.close()

    cfg = tier_section_config(capture_pack.config, kind="tool")
    detail = build_kind_detail(
        {(EntityKind.TOOL, capture_pack.tool.entity_id): state},
        kind=EntityKind.TOOL,
        cfg=cfg,
        session_id=1,
        config=capture_pack.config,
        tracked_catalog_entity_ids=frozenset({capture_pack.tool.entity_id}),
    )
    row = next(
        item
        for items in detail["by_tier"].values()
        for item in items
        if item["entity_id"] == capture_pack.tool.entity_id
    )
    assert row["stats"]["attempts"] == scenario.expected["attempts"]
    assert row["scores"]["execution"] == execution_score(state.stats)
    assert row["scores"]["utility"] == utility_score(state.stats)


def test_build_payload_from_capture_fixture(tmp_path: Path) -> None:
    tool = load_capture_tool()
    config = sample_aggregator_config(catalog_scope="workspace", workspace_root=tmp_path)
    payload = _build_payload(
        config=config,
        tool_name=tool.wire_name,
        args={"queries": ["fixture"]},
        success=True,
        catalog_content_hash="fixture-hash",
        mcp_server=tool.mcp_server,
        bare_tool_name=tool.bare_tool_name,
        input_schema=tool.input_schema,
    )
    assert payload is not None
    assert payload["tool_name"] == tool.wire_name
    assert payload["source"] == "cyt_mcp"
    assert payload["catalog_content_hash"] == "fixture-hash"
    assert payload["mcp_server"] == tool.mcp_server
    assert payload["bare_tool_name"] == tool.bare_tool_name


def test_capture_config_readers_match_fixture_defaults() -> None:
    values = load_capture_config_values()
    default_cfg = {"tools": {"tiers": {"capture": {"source": values["capture_default"]}}}}
    hooks_cfg = {"tools": {"tiers": {"capture": {"source": values["capture_hooks"]}}}}
    assert tier_tool_capture_source(default_cfg) == "cyt_mcp"
    assert tier_tool_capture_via_hooks(default_cfg) is False
    assert tier_tool_capture_source(hooks_cfg) == "hooks"
    assert tier_tool_capture_via_hooks(hooks_cfg) is True


@pytest.mark.parametrize("tool_name", load_meta_tools_not_reported())
def test_meta_tools_are_not_reported(tool_name: str) -> None:
    assert _is_meta_tool(tool_name) is True


def test_tracked_tool_is_reported_by_middleware() -> None:
    tool = load_capture_tool()
    assert _is_meta_tool(tool.wire_name) is False


@pytest.mark.asyncio
async def test_middleware_skips_meta_tools_from_fixture(
    capture_pack: TierCaptureFixturePack,
) -> None:
    from cyt_mcp.config_holder import ConfigHolder
    from cyt_mcp.runtime_cache import RuntimeToolCache

    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=capture_pack.workspace,
    )
    cache = RuntimeToolCache()
    holder = ConfigHolder(config)
    middleware = ToolUseFeedbackMiddleware(MagicMock(), cache, holder, config=config)
    scheduled: list[str] = []

    params = CallToolRequestParams(name="get-tool-definitions", arguments={})
    context = MagicMock()
    context.message = params
    ok_result = ToolResult(content=[TextContent(type="text", text="ok")])

    with patch(
        "cyt_mcp.tool_use_feedback_middleware.schedule_tool_use_feedback",
        side_effect=lambda **kwargs: scheduled.append(kwargs["tool_name"]),
    ):
        await middleware.on_call_tool(context, AsyncMock(return_value=ok_result))

    assert scheduled == []


def test_normalize_hook_base_url_strips_tier_feedback_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = normalize_hook_base_url("http://127.0.0.1:8834/hook/tier/feedback")
    assert base == "http://127.0.0.1:8834"
    monkeypatch.setenv("CYT_HOOK_URL", "http://127.0.0.1:8834/hook/connect")
    assert resolve_hook_path("/hook/tier/feedback") == "http://127.0.0.1:8834/hook/tier/feedback"


def test_capture_tool_dict_matches_fixture_entity_id() -> None:
    tool = load_capture_tool()
    from cyt.tiers.adapters.tools import tool_entity_id

    assert tool_entity_id(capture_tool_dict(tool)) == tool.entity_id


def test_materialize_capture_pack_has_git_root(tmp_path: Path) -> None:
    pack = materialize_capture_pack(tmp_path)
    assert (pack.workspace / ".git").is_dir()
    assert pack.tool.entity_id.startswith("cyt_mcp:")
