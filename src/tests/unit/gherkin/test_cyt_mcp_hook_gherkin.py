"""Gherkin steps for cyt-mcp hook integration (injection, session log, disk cache)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.cyt_mcp.catalog import (
    _normalize_catalog_payload,
    apply_fetched_catalog,
    clear_cyt_mcp_catalog_cache,
)
from cyt.cyt_mcp.catalog_disk import read_disk_catalog
from cyt.injection.session_log_build import build_tool_log_entry, tool_item_key
from cyt.tools.source_inject import (
    format_cyt_mcp_source_section,
    format_multi_source_agent_tools,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_hook.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _reset_cyt_mcp_catalog() -> Iterator[None]:
    clear_cyt_mcp_catalog_cache()
    yield
    clear_cyt_mcp_catalog_cache()


def _filesystem_tool() -> dict:
    return {
        "name": "filesystem_read_file",
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        "cyt_catalog_source": "cyt_mcp",
    }


@given("a cyt-mcp tool definition for filesystem_read_file")
def given_cyt_mcp_tool(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {"tools": [_filesystem_tool()]}


@when("cyt-mcp source section is formatted")
def when_format_cyt_mcp_section(gherkin_context: GherkinContext) -> None:
    gherkin_context.stdout = format_cyt_mcp_source_section(gherkin_context.payload["tools"])


@then("formatted text should include a cyt-mcp XML block")
def then_cyt_mcp_block(gherkin_context: GherkinContext) -> None:
    assert "<cyt-mcp>" in gherkin_context.stdout
    assert "</cyt-mcp>" in gherkin_context.stdout


@then(parsers.parse("formatted text should include the tool name {name}"))
def then_tool_name_in_text(name: str, gherkin_context: GherkinContext) -> None:
    assert name in gherkin_context.stdout


@given("cyt-mcp and executor source sections")
def given_multi_sections(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "sections": {
            "cyt_mcp": "<cyt-mcp>\ny\n</cyt-mcp>",
            "executor": "<executor>\nx\n</executor>",
        },
    }


@when("multi-source agent-tools text is assembled")
def when_multi_source(gherkin_context: GherkinContext) -> None:
    gherkin_context.stdout = format_multi_source_agent_tools(gherkin_context.payload["sections"])


@then("cyt-mcp section should appear before executor section")
def then_cyt_mcp_first(gherkin_context: GherkinContext) -> None:
    text = gherkin_context.stdout
    assert text.index("<cyt-mcp>") < text.index("<executor>")


@given("a cyt-mcp catalog tool filesystem_read_file")
def given_catalog_tool(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {"tool": _filesystem_tool()}


@when("a session tool log entry is built for cyt_mcp")
def when_build_log_entry(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["entry"] = build_tool_log_entry(
        gherkin_context.payload["tool"],
        catalog="cyt_mcp",
        full=True,
    )


@then("log entry catalog should be cyt_mcp")
def then_log_catalog(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["entry"]["catalog"] == "cyt_mcp"


@then(parsers.parse("log entry key should be {key}"))
def then_log_key(key: str, gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["entry"]["key"] == key
    assert tool_item_key(gherkin_context.payload["tool"], catalog="cyt_mcp") == key


@then("log entry should include input_schema")
def then_log_schema(gherkin_context: GherkinContext) -> None:
    schema = gherkin_context.payload["entry"].get("input_schema")
    assert isinstance(schema, dict)
    assert "path" in schema.get("properties", {})


@given("a cyt-mcp catalog payload from live fetch")
def given_live_catalog(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    tools = [_filesystem_tool()]
    gherkin_context.payload = {
        "tools": tools,
        "config": {
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
                "tools": {
                    "enabled": True,
                    "hook": {"tools_from": ["cyt_mcp"], "cyt_mcp": {"agent": "cursor"}},
                },
            },
        },
        "tmp_path": tmp_path,
    }


@when("the hook daemon applies fetched catalog to disk")
def when_apply_disk(gherkin_context: GherkinContext) -> None:
    from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache

    clear_cyt_mcp_catalog_cache()
    apply_fetched_catalog(gherkin_context.payload["config"], gherkin_context.payload["tools"])


@then("disk catalog should contain the same tool names")
def then_disk_names(gherkin_context: GherkinContext) -> None:
    envelope = read_disk_catalog("cursor")
    assert envelope is not None
    names = [tool["name"] for tool in envelope["tools"]]
    assert names == ["filesystem_read_file"]


@then("a cold hydrate should load tools into memory")
def then_cold_hydrate(gherkin_context: GherkinContext) -> None:
    from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache, load_cyt_mcp_catalog_from_disk

    clear_cyt_mcp_catalog_cache()
    assert load_cyt_mcp_catalog_from_disk(gherkin_context.payload["config"]) is True
    from cyt.cyt_mcp.catalog import get_cyt_mcp_catalog

    tools = get_cyt_mcp_catalog(gherkin_context.payload["config"], blocking=False)
    assert tools is not None
    assert tools[0]["name"] == "filesystem_read_file"


@given(parsers.parse("a cyt-mcp catalog JSON tool named {name}"))
def given_catalog_json_tool(name: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "payload": {
            "tools": [
                {
                    "name": name,
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
        },
        "name": name,
    }


@when("the hook catalog normalizer processes the payload")
def when_normalize(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["normalized"] = _normalize_catalog_payload(
        gherkin_context.payload["payload"],
    )


@then(parsers.parse("normalized tool name should remain {name}"))
def then_normalized_name(name: str, gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["normalized"][0]["name"] == name
