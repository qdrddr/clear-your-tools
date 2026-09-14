"""Integration tests: cyt-mcp tool-use capture through hook daemon HTTP handler."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.manager import _managers
from cyt.tiers.models import EntityKind
from cyt.tool_examples.store import ToolExamplesStore
from tests.support.tier_capture_fixtures import (
    IntegrationCaptureScenario,
    TierCaptureFixturePack,
    http_payload_by_id,
    load_integration_capture_scenarios,
    materialize_capture_pack,
    post_tier_feedback_http,
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    load_integration_capture_scenarios(),
    ids=[item.id for item in load_integration_capture_scenarios()],
)
@pytest.mark.asyncio
async def test_http_capture_sequences_update_tier_stats(
    capture_pack: TierCaptureFixturePack,
    scenario: IntegrationCaptureScenario,
) -> None:
    examples_enabled = bool(scenario.expected.get("examples_captured"))
    for payload_id in scenario.payload_ids:
        payload = http_payload_by_id(payload_id).payload
        status = await post_tier_feedback_http(
            capture_pack,
            payload,
            examples_enabled=examples_enabled,
        )
        assert status == 204

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(capture_pack.config, workspace=capture_pack.workspace)
    state = manager._states.get((EntityKind.TOOL, capture_pack.tool.entity_id))
    assert state is not None
    assert state.stats.attempts == scenario.expected["attempts"]
    assert state.stats.used == scenario.expected["used"]

    if examples_enabled:
        store = ToolExamplesStore.open(str(capture_pack.examples_db_path))
        try:
            project_id = store.get_or_create_project(str(capture_pack.workspace))
            rows = store.list_captures(
                project_id,
                capture_pack.tool.mcp_server,
                capture_pack.tool.bare_tool_name,
            )
            assert rows
        finally:
            store.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_attempt_does_not_capture_examples(
    capture_pack: TierCaptureFixturePack,
) -> None:
    payload = http_payload_by_id("cyt_mcp_failed_attempt").payload
    status = await post_tier_feedback_http(
        capture_pack,
        payload,
        examples_enabled=True,
    )
    assert status == 204

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(capture_pack.config, workspace=capture_pack.workspace)
    state = manager._states.get((EntityKind.TOOL, capture_pack.tool.entity_id))
    assert state is not None
    assert state.stats.attempts == 1.0
    assert state.stats.used == 0.0

    store = ToolExamplesStore.open(str(capture_pack.examples_db_path))
    try:
        project_id = store.get_or_create_project(str(capture_pack.workspace))
        rows = store.list_captures(
            project_id,
            capture_pack.tool.mcp_server,
            capture_pack.tool.bare_tool_name,
        )
        assert not rows
    finally:
        store.close()
