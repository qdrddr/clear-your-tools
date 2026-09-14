"""Unit tests for tier pipeline source shaping (pre-BM25 tool/skill preparation)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cyt.skills.catalog import SkillEntryRef
from cyt.tiers.adapters.skills import (
    prepare_skill_entries_for_tier_search,
    prepare_skill_entry_for_tier_search,
)
from cyt.tiers.adapters.tools import (
    prepare_tool_for_tier_pipeline,
    prepare_tools_for_tier_pipeline,
    tool_entity_id,
)
from cyt.tiers.models import Tier
from cyt.tools.inject import format_tool_item
from tests.support.tier_behavior_fixtures import (
    PipelineSkillExpectation,
    PipelineToolExpectation,
    TierBehaviorFixturePack,
    build_registry_from_pack,
    iter_pipeline_skill_cases,
    iter_pipeline_tool_cases,
    load_pipeline_skill_expectations,
    load_pipeline_tool_expectations,
    materialize_fixture_pack,
    skill_fixture_key,
    tool_by_entity_id,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    return materialize_fixture_pack(tmp_path)


def _entry_with_source_markdown(entry: SkillEntryRef) -> SkillEntryRef:
    """Disk-backed registry entries omit inline markdown; load from source for pipeline tests."""
    markdown = str(entry.document.get("markdown") or entry.document.get("content") or "")
    if not markdown.strip():
        source = Path(entry.source_path)
        if source.is_file():
            markdown = source.read_text(encoding="utf-8")
    document = dict(entry.document)
    document["markdown"] = markdown
    document["content"] = markdown
    return replace(entry, document=document)


def test_pipeline_fixture_sections_are_self_consistent() -> None:
    tool_pipeline = load_pipeline_tool_expectations()
    skill_pipeline = load_pipeline_skill_expectations()
    assert len(tool_pipeline) >= 4
    assert len(skill_pipeline) >= 3
    assert len(iter_pipeline_tool_cases()) == len(tool_pipeline)
    assert len(iter_pipeline_skill_cases()) == len(skill_pipeline)


@pytest.mark.parametrize(
    ("entity_id", "tool_name", "expectation"),
    [
        (entity_id, tool_name, expectation)
        for entity_id, tool_name, expectation in iter_pipeline_tool_cases()
    ],
    ids=[
        f"{tool_name}-pipeline-{expectation.tier.name}"
        for _entity_id, tool_name, expectation in iter_pipeline_tool_cases()
    ],
)
def test_prepare_tool_for_tier_pipeline_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    entity_id: str,
    tool_name: str,
    expectation: PipelineToolExpectation,
) -> None:
    tool = tool_by_entity_id(fixture_pack, entity_id)
    prepared = prepare_tool_for_tier_pipeline(tool, expectation.tier)
    schema = prepared.get("input_schema") or prepared.get("inputSchema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    prop_keys = list(properties.keys()) if isinstance(properties, dict) else []
    assert prop_keys == list(expectation.pipeline_schema_property_keys)

    formatted = format_tool_item(prepared)
    if expectation.injection_omits_schema:
        assert "input_schema" not in formatted
    else:
        assert "'input_schema':" in formatted


@pytest.mark.parametrize(
    ("doc_id", "expectation"),
    [(doc_id, expectation) for doc_id, expectation in iter_pipeline_skill_cases()],
    ids=[
        f"{doc_id}-search-prep-{expectation.tier.name}"
        for doc_id, expectation in iter_pipeline_skill_cases()
    ],
)
def test_prepare_skill_entry_for_tier_search_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    doc_id: str,
    expectation: PipelineSkillExpectation,
) -> None:
    entries = build_registry_from_pack(fixture_pack)
    entry = _entry_with_source_markdown(
        next(item for item in entries if skill_fixture_key(item) == doc_id),
    )
    if expectation.search_representation == "unchanged":
        prepared = prepare_skill_entry_for_tier_search(entry, expectation.tier)
        original = str(entry.document.get("markdown") or entry.document.get("content") or "")
        prepared_text = str(
            prepared.document.get("markdown") or prepared.document.get("content") or "",
        )
        assert prepared_text == original
        return

    prepared = prepare_skill_entry_for_tier_search(entry, expectation.tier)
    markdown = str(prepared.document.get("markdown") or prepared.document.get("content") or "")
    for fragment in expectation.search_markdown_includes:
        assert fragment in markdown
    for fragment in expectation.search_markdown_excludes:
        assert fragment not in markdown


def test_prepare_tools_for_tier_pipeline_preserves_order_and_mixed_tiers(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    tools = list(fixture_pack.tools)
    tier_by_tool = {
        tool_entity_id(tools[0]): Tier.COLD,
        tool_entity_id(tools[1]): Tier.ACTIVE,
        tool_entity_id(tools[2]): Tier.HOT,
    }
    prepared = prepare_tools_for_tier_pipeline(tools, tier_by_tool)
    assert [tool.get("name") for tool in prepared] == [tool.get("name") for tool in tools]
    assert prepared[0].get("input_schema") == {}
    assert list(prepared[1]["input_schema"]["properties"].keys()) == ["queries"]
    assert "search_query" in prepared[2]["input_schema"]["properties"]


def test_prepare_skill_entries_for_tier_search_batch(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    from cyt.tiers.adapters.skills import skill_entity_id

    entries = [
        _entry_with_source_markdown(entry) for entry in build_registry_from_pack(fixture_pack)
    ]
    tier_by_skill = {
        skill_entity_id(entry): Tier.COLD for entry in entries if skill_entity_id(entry)
    }
    prepared = prepare_skill_entries_for_tier_search(entries, tier_by_skill)
    assert len(prepared) == len(entries)
    for entry in prepared:
        markdown = str(entry.document.get("markdown") or entry.document.get("content") or "")
        assert "## Usage" not in markdown
        assert "## Workflow" not in markdown
        assert "## Steps" not in markdown
