"""Tests for resource injection formatting."""

from __future__ import annotations

from cyt.injection.pre_exposed import filter_pre_exposed_resources
from cyt.resources.inject import MatchedResource, format_agent_resources, format_resource_item


def _resource() -> MatchedResource:
    return MatchedResource(
        doc_id="architecture",
        file_path="mcpc/everything/resources/architecture.md",
        markdown="---\nname: architecture.md\ndescription: Static document\n---\n\n# Architecture\n",
        name="architecture.md",
        command="mcpc --json @everything resources-read demo://architecture.md",
        description="Static document",
        score=1.0,
        token_count=10,
    )


def test_format_agent_resources_emits_command_attr() -> None:
    text = format_agent_resources([_resource()])
    assert "<agent-resources>" in text
    assert "command='mcpc --json @everything resources-read demo://architecture.md'" in text
    assert "description='Static document'" in text
    assert "# Architecture" in text


def test_filter_pre_exposed_resources_drops_verbatim_fragment() -> None:
    resource = _resource()
    session_text = format_resource_item(resource)
    filtered = filter_pre_exposed_resources([resource], session_text)
    assert filtered == []
