"""Fixture loader for cyt_mcp session log wire name regression tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.injection.session_log_build import build_tool_log_entry
from tests.support.inject_preview_fixtures import InjectPreviewFixturePack

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_session_log_wire_name"
CATALOG_TOOLS_PATH = FIXTURES_ROOT / "catalog_tools.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
LEGACY_ENTRIES_PATH = FIXTURES_ROOT / "legacy_session_entries.json"

SessionEntrySource = Literal["wire_tool_log_entry", "legacy_bare_grep"]


@dataclass(frozen=True)
class CatalogToolFixture:
    wire_name: str
    tool: dict[str, Any]


@dataclass(frozen=True)
class ToolLogWriteExpectation:
    id: str
    wire_name: str
    expected_key: str
    expected_xml_name_attr: str
    must_not_xml_name_attr: str


@dataclass(frozen=True)
class GateSkipExpectation:
    id: str
    wire_name: str
    session_entry_source: SessionEntrySource
    expect_skipped: bool


@dataclass(frozen=True)
class InjectPreviewExpectation:
    id: str
    wire_name: str
    session_entry_source: SessionEntrySource
    expect_injection_contains: str
    expect_injection_excludes: str | None


@dataclass(frozen=True)
class LegacySessionEntry:
    id: str
    issue: str
    entry: dict[str, Any]


@dataclass(frozen=True)
class WireNameFixturePack:
    catalog_tools: tuple[CatalogToolFixture, ...]
    tool_log_write_expectations: tuple[ToolLogWriteExpectation, ...]
    gate_skip_expectations: tuple[GateSkipExpectation, ...]
    inject_preview_expectations: tuple[InjectPreviewExpectation, ...]
    legacy_session_entries: tuple[LegacySessionEntry, ...]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_catalog_tools(path: Path = CATALOG_TOOLS_PATH) -> tuple[CatalogToolFixture, ...]:
    payload = _load_json(path)
    raw = payload.get("tools")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected tools array")
    out: list[CatalogToolFixture] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        wire_name = str(item.get("name") or "").strip()
        if not wire_name:
            continue
        out.append(CatalogToolFixture(wire_name=wire_name, tool=dict(item)))
    return tuple(out)


def load_legacy_session_entries(path: Path = LEGACY_ENTRIES_PATH) -> tuple[LegacySessionEntry, ...]:
    payload = _load_json(path)
    raw = payload.get("entries")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected entries array")
    out: list[LegacySessionEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if not isinstance(entry, dict):
            continue
        out.append(
            LegacySessionEntry(
                id=str(item.get("id") or ""),
                issue=str(item.get("issue") or ""),
                entry=dict(entry),
            ),
        )
    return tuple(out)


def _parse_tool_log_write(item: dict[str, Any]) -> ToolLogWriteExpectation:
    return ToolLogWriteExpectation(
        id=str(item.get("id") or ""),
        wire_name=str(item.get("wire_name") or ""),
        expected_key=str(item.get("expected_key") or ""),
        expected_xml_name_attr=str(item.get("expected_xml_name_attr") or ""),
        must_not_xml_name_attr=str(item.get("must_not_xml_name_attr") or ""),
    )


def _parse_gate_skip(item: dict[str, Any]) -> GateSkipExpectation:
    source_raw = str(item.get("session_entry_source") or "wire_tool_log_entry")
    if source_raw not in {"wire_tool_log_entry", "legacy_bare_grep"}:
        raise ValueError(f"unknown session_entry_source: {source_raw!r}")
    return GateSkipExpectation(
        id=str(item.get("id") or ""),
        wire_name=str(item.get("wire_name") or ""),
        session_entry_source=source_raw,  # type: ignore[arg-type]
        expect_skipped=bool(item.get("expect_skipped")),
    )


def _parse_inject_preview(item: dict[str, Any]) -> InjectPreviewExpectation:
    source_raw = str(item.get("session_entry_source") or "wire_tool_log_entry")
    if source_raw not in {"wire_tool_log_entry", "legacy_bare_grep"}:
        raise ValueError(f"unknown session_entry_source: {source_raw!r}")
    excludes = item.get("expect_injection_excludes")
    return InjectPreviewExpectation(
        id=str(item.get("id") or ""),
        wire_name=str(item.get("wire_name") or ""),
        session_entry_source=source_raw,  # type: ignore[arg-type]
        expect_injection_contains=str(item.get("expect_injection_contains") or ""),
        expect_injection_excludes=str(excludes).strip() if excludes else None,
    )


def load_tool_log_write_expectations(
    path: Path = SCENARIOS_PATH,
) -> tuple[ToolLogWriteExpectation, ...]:
    payload = _load_json(path)
    raw = payload.get("tool_log_write_expectations")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected tool_log_write_expectations array")
    return tuple(_parse_tool_log_write(item) for item in raw if isinstance(item, dict))


def load_gate_skip_expectations(path: Path = SCENARIOS_PATH) -> tuple[GateSkipExpectation, ...]:
    payload = _load_json(path)
    raw = payload.get("gate_skip_expectations")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected gate_skip_expectations array")
    return tuple(_parse_gate_skip(item) for item in raw if isinstance(item, dict))


def load_inject_preview_expectations(
    path: Path = SCENARIOS_PATH,
) -> tuple[InjectPreviewExpectation, ...]:
    payload = _load_json(path)
    raw = payload.get("inject_preview_expectations")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected inject_preview_expectations array")
    return tuple(_parse_inject_preview(item) for item in raw if isinstance(item, dict))


def load_fixture_pack() -> WireNameFixturePack:
    return WireNameFixturePack(
        catalog_tools=load_catalog_tools(),
        tool_log_write_expectations=load_tool_log_write_expectations(),
        gate_skip_expectations=load_gate_skip_expectations(),
        inject_preview_expectations=load_inject_preview_expectations(),
        legacy_session_entries=load_legacy_session_entries(),
    )


def catalog_tools_list(path: Path = CATALOG_TOOLS_PATH) -> list[dict[str, Any]]:
    return [fixture.tool for fixture in load_catalog_tools(path)]


def catalog_tool_by_wire_name(wire_name: str) -> dict[str, Any]:
    for fixture in load_catalog_tools():
        if fixture.wire_name == wire_name:
            return dict(fixture.tool)
    raise KeyError(f"unknown wire name in catalog fixture: {wire_name!r}")


def legacy_entry_by_id(entry_id: str) -> dict[str, Any]:
    for legacy in load_legacy_session_entries():
        if legacy.id == entry_id:
            return dict(legacy.entry)
    raise KeyError(f"unknown legacy session entry id: {entry_id!r}")


def session_entry_for_source(
    source: SessionEntrySource,
    *,
    wire_name: str,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if source == "legacy_bare_grep":
        return legacy_entry_by_id("legacy_bare_grep")
    tool = catalog_tool_by_wire_name(wire_name)
    return build_tool_log_entry(
        tool,
        catalog="cyt_mcp",
        full=True,
        catalog_tools=catalog_tools or catalog_tools_list(),
    )


def materialize_inject_preview_pack_with_wire_catalog(
    tmp_path: Path,
) -> InjectPreviewFixturePack:
    """Build inject preview workspace pack using this fixture's catalog tools."""
    from dataclasses import replace

    from tests.support.inject_preview_fixtures import materialize_fixture_pack

    pack = materialize_fixture_pack(tmp_path)
    return replace(pack, tools=catalog_tools_list())
