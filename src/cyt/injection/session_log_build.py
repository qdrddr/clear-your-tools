"""Build native JSON session-log records from injected tool/skill/resource items."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from cyt.common.paths import shorten_home_path
from cyt.resources.inject import MatchedResource
from cyt.skills.catalog import content_sha256_for_file
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import format_tool_item
from cyt.tools.mcpc_inject import _format_mcpc_tool_item, _mcpc_injection_schema_body

CatalogKind = Literal["executor", "mcpc", "definitions", "cloudflare", "cyt_mcp"]
ItemKind = Literal["tool", "skill", "resource", "tool_catalog", "session_state"]

_CATALOG_SOURCE_ORDER: tuple[CatalogKind, ...] = (
    "cyt_mcp",
    "mcpc",
    "cloudflare",
    "executor",
    "definitions",
)

_TOOL_DEF_HASH_PREFIX = b"v1-tool-def\x00"

_SKILL_HASH_BY_SOURCE: dict[str, str] | None = None


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tool_definition_content_hash(definition: dict[str, Any]) -> str:
    """Match ``tool_definition_content_hash`` in cyt-indexer (``~/.config/cyt/tools/entries/{hash}/``)."""
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_TOOL_DEF_HASH_PREFIX + canonical.encode("utf-8")).hexdigest()


def _mcpc_tool_definition_for_hash(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    name = str(tool.get("name") or "").strip()
    if not name:
        session = str(tool.get("mcpc_session") or "").strip()
        tool_name = str(tool.get("tool_name") or tool.get("name") or "").strip()
        name = f"{session}/{tool_name}" if session else tool_name
    definition: dict[str, Any] = {
        "name": name,
        "id": name,
        "inputSchema": schema if isinstance(schema, dict) else {},
    }
    if tool.get("description") is not None:
        definition["description"] = str(tool["description"])
    return definition


def _executor_tool_definition_for_hash(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema") or tool.get("parameters") or tool.get("inputSchema") or {}
    tool_name = str(tool.get("tool_name") or "").strip()
    name = str(tool.get("name") or "").strip()
    hash_name = name or tool_name
    if tool_name:
        source = str(tool.get("cyt_catalog_source") or "").strip()
        if source == "cyt_mcp" or (name and name != tool_name and name.endswith(f"_{tool_name}")):
            hash_name = tool_name
    definition: dict[str, Any] = {
        "name": hash_name,
        "input_schema": schema if isinstance(schema, dict) else {},
    }
    if tool.get("description") is not None:
        definition["description"] = str(tool["description"])
    return definition


def _original_tool_definition_for_hash(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
) -> dict[str, Any]:
    full_schema = tool.get("full_schema")
    if isinstance(full_schema, dict):
        return full_schema
    if catalog == "mcpc":
        return _mcpc_tool_definition_for_hash(tool)
    return _executor_tool_definition_for_hash(tool)


def _load_skill_hash_by_source() -> dict[str, str]:
    global _SKILL_HASH_BY_SOURCE
    if _SKILL_HASH_BY_SOURCE is not None:
        return _SKILL_HASH_BY_SOURCE
    index: dict[str, str] = {}
    entries_root = Path("~/.config/cyt/skills/entries").expanduser()
    if entries_root.is_dir():
        for entry_dir in entries_root.iterdir():
            if not entry_dir.is_dir():
                continue
            meta_path = entry_dir / "metadata.json"
            if not meta_path.is_file():
                continue
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            source_path = str(metadata.get("source_path") or "").strip()
            if source_path:
                index[source_path] = entry_dir.name
    _SKILL_HASH_BY_SOURCE = index
    return index


def _resolve_tool_for_hash(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not catalog_tools:
        return tool
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    session = str(tool.get("mcpc_session") or "").strip()
    tool_source = str(tool.get("cyt_catalog_source") or catalog).strip()
    for original in catalog_tools:
        orig_name = str(original.get("tool_name") or original.get("name") or "").strip()
        if orig_name != name:
            continue
        orig_source = str(original.get("cyt_catalog_source") or "").strip()
        if tool_source and orig_source and orig_source != tool_source:
            continue
        if (
            catalog == "mcpc"
            and session
            and str(original.get("mcpc_session") or "").strip() != session
        ):
            continue
        return original
    return tool


def tool_content_hash(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> str:
    explicit = tool.get("content_hash")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source = _resolve_tool_for_hash(tool, catalog=catalog, catalog_tools=catalog_tools)
    definition = _original_tool_definition_for_hash(source, catalog=catalog)
    return tool_definition_content_hash(definition)


def skill_content_hash(match: MatchedSkill) -> str:
    if match.content_hash:
        return match.content_hash

    path = shorten_home_path(match.file_path).strip()
    if path:
        cached = _load_skill_hash_by_source().get(path)
        if cached:
            return cached

        try:
            resolved = Path(path).expanduser()
            if resolved.is_file():
                return content_sha256_for_file(resolved)
        except OSError:
            pass

    return hashlib.sha256(match.markdown.encode("utf-8")).hexdigest()


def resource_content_hash(match: MatchedResource) -> str:
    if match.content_hash:
        return match.content_hash

    path = shorten_home_path(match.file_path).strip()
    if path:
        cached = _load_skill_hash_by_source().get(path)
        if cached:
            return cached
        try:
            resolved = Path(path).expanduser()
            if resolved.is_file():
                return content_sha256_for_file(resolved)
        except OSError:
            pass
    return hashlib.sha256(match.markdown.encode("utf-8")).hexdigest()


def tool_item_key(tool: dict[str, Any], *, catalog: CatalogKind | None = None) -> str:
    source = str(tool.get("cyt_catalog_source") or catalog or "executor").strip()
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    if source == "mcpc":
        session = str(tool.get("mcpc_session") or "").strip()
        return f"tool:mcpc:{session}:{name}"
    if source == "cyt_mcp":
        return f"tool:cyt_mcp:{name}"
    if source == "cloudflare":
        return f"tool:cloudflare:{name}"
    if source == "definitions":
        return f"tool:definitions:{name}"
    return f"tool:executor:{name}"


def tool_item_legacy_keys(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind | None = None,
) -> tuple[str, ...]:
    """Pre-multi-source session-log keys for the same tool (lookup aliases only)."""
    source = str(tool.get("cyt_catalog_source") or catalog or "executor").strip()
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    if not name:
        return ()
    legacy: list[str] = []
    if source == "mcpc":
        session = str(tool.get("mcpc_session") or "").strip()
        if session:
            legacy.append(f"tool:{session}:{name}")
    legacy.append(f"tool:{name}")
    current = tool_item_key(tool, catalog=catalog)
    return tuple(key for key in legacy if key != current)


def skill_item_key(match: MatchedSkill, *, command: str | None = None) -> str:
    if command:
        return f"skill:cmd:{command}"
    path = shorten_home_path(match.file_path)
    return f"skill:{path}"


def resource_item_key(match: MatchedResource) -> str:
    command = match.command.strip()
    return f"resource:{command}"


def _skill_command(match: MatchedSkill) -> str | None:
    if match.command:
        return match.command.strip()
    from cyt.skills.inject import _resolve_skill_command

    return _resolve_skill_command(match)


def _skill_source(match: MatchedSkill, command: str | None) -> Literal["file", "mcpc"]:
    if command:
        return "mcpc"
    return "file"


def _skill_body(match: MatchedSkill, *, full: bool) -> str:
    if full:
        return match.markdown.rstrip()
    from cyt.skills.frontmatter import injection_markdown_body

    return injection_markdown_body(match.markdown).rstrip()


def _resource_body(match: MatchedResource, *, full: bool) -> str:
    if full:
        return match.markdown.rstrip()
    from cyt.skills.frontmatter import injection_markdown_body

    return injection_markdown_body(match.markdown).rstrip()


def format_tool_fragment(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
    full: bool = False,
    include_tool_description: bool = True,
) -> str:
    if catalog == "mcpc":
        return _format_mcpc_tool_item(tool, include_description=include_tool_description)
    return format_tool_item(tool, include_tool_description=include_tool_description)


def format_skill_fragment(match: MatchedSkill, *, full: bool = False) -> str:
    from cyt.skills import inject as skill_inject

    return skill_inject.format_skill_item(match, full=full)


def format_resource_fragment(match: MatchedResource, *, full: bool = False) -> str:
    from cyt.resources import inject as resource_inject

    return resource_inject.format_resource_item(match, full=full)


def format_entry_fragment(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    full = bool(entry.get("full"))
    if kind == "tool":
        tool = _tool_dict_from_log_entry(entry)
        raw_catalog = entry.get("catalog", "executor")
        if raw_catalog == "mcpc":
            catalog: CatalogKind = "mcpc"
        elif raw_catalog == "cloudflare":
            catalog = "cloudflare"
        elif raw_catalog == "definitions":
            catalog = "definitions"
        elif raw_catalog == "cyt_mcp":
            catalog = "cyt_mcp"
        else:
            catalog = "executor"
        include_description = "description" in entry
        return format_tool_fragment(
            tool,
            catalog=catalog,
            full=full,
            include_tool_description=include_description,
        )
    if kind == "skill":
        skill_match = _skill_match_from_log_entry(entry)
        return format_skill_fragment(skill_match, full=full)
    if kind == "resource":
        resource_match = _resource_match_from_log_entry(entry)
        return format_resource_fragment(resource_match, full=full)
    return ""


def build_tool_log_entry(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
    full: bool,
    include_tool_description: bool = True,
    server: dict[str, str] | None = None,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    key = tool_item_key(tool, catalog=catalog)
    content_hash = tool_content_hash(tool, catalog=catalog, catalog_tools=catalog_tools)
    entry: dict[str, Any] = {
        "kind": "tool",
        "key": key,
        "hash": content_hash,
        "full": full,
        "catalog": catalog,
        "name": str(tool.get("tool_name") or tool.get("name") or "").strip(),
    }
    if catalog == "mcpc":
        entry["title"] = str(tool.get("title") or entry["name"]).strip()
        entry["mcpc_session"] = str(tool.get("mcpc_session") or "").strip()
        schema = _mcpc_injection_schema_body(tool)
        entry["input_schema"] = deepcopy(schema)
        from cyt.tools.mcpc_inject import _cli_example

        entry["cli"] = _cli_example(entry["mcpc_session"], entry["name"], schema)
        if include_tool_description:
            description = str(tool.get("description") or "").strip()
            if description:
                entry["description"] = description
        if server:
            entry["server"] = {k: v for k, v in server.items() if v}
    elif catalog == "cyt_mcp":
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        entry["input_schema"] = deepcopy(schema if isinstance(schema, dict) else {})
        entry["source"] = "hook_injection"
        if include_tool_description:
            description = str(tool.get("description") or "").strip()
            if description:
                entry["description"] = description
    else:
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        entry["input_schema"] = deepcopy(schema if isinstance(schema, dict) else {})
        if include_tool_description:
            description = str(tool.get("description") or "").strip()
            if description:
                entry["description"] = description
    return entry


def build_skill_log_entry(match: MatchedSkill, *, full: bool) -> dict[str, Any]:
    command = _skill_command(match)
    source = _skill_source(match, command)
    key = skill_item_key(match, command=command)
    body = _skill_body(match, full=full)
    entry: dict[str, Any] = {
        "kind": "skill",
        "key": key,
        "hash": skill_content_hash(match),
        "full": full,
        "source": source,
        "body": body,
    }
    if match.name:
        entry["name"] = match.name
    if source == "file":
        entry["path"] = shorten_home_path(match.file_path)
    if command:
        entry["command"] = command
    return entry


def _tool_input_schema_for_catalog(tool: dict[str, Any], *, catalog: CatalogKind) -> dict[str, Any]:
    full_schema = tool.get("full_schema")
    if isinstance(full_schema, dict):
        schema = full_schema.get("input_schema") or full_schema.get("inputSchema")
        if isinstance(schema, dict):
            return deepcopy(schema)
    for key in ("input_schema", "inputSchema", "parameters"):
        raw = tool.get(key)
        if isinstance(raw, dict) and raw:
            return deepcopy(raw)
    if catalog == "mcpc":
        return deepcopy(_mcpc_injection_schema_body(tool))
    return {}


def _tool_record_core_for_catalog_bundle(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
) -> dict[str, Any]:
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    record: dict[str, Any] = {
        "name": name,
        "input_schema": _tool_input_schema_for_catalog(tool, catalog=catalog),
    }
    description = tool.get("description")
    if description is not None and str(description).strip():
        record["description"] = str(description).strip()
    if catalog == "mcpc":
        session = str(tool.get("mcpc_session") or "").strip()
        if session:
            record["mcpc_session"] = session
    return record


def catalog_tool_record_content_hash(catalog: CatalogKind, record: dict[str, Any]) -> str:
    """Per-tool hash aligned with Type-1 ``tool_content_hash`` for the same full schema."""
    if catalog == "mcpc":
        return tool_definition_content_hash(_mcpc_tool_definition_for_hash(record))
    return tool_definition_content_hash(_executor_tool_definition_for_hash(record))


def _tool_record_for_catalog_bundle(
    tool: dict[str, Any],
    *,
    catalog: CatalogKind,
) -> dict[str, Any]:
    record = _tool_record_core_for_catalog_bundle(tool, catalog=catalog)
    record["hash"] = catalog_tool_record_content_hash(catalog, record)
    return record


def catalog_bundle_content_hash(catalog: CatalogKind, tools: list[dict[str, Any]]) -> str:
    canonical_tools = sorted(
        [
            _tool_record_core_for_catalog_bundle(tool, catalog=catalog)
            for tool in tools
            if str(tool.get("tool_name") or tool.get("name") or "").strip()
        ],
        key=lambda item: (
            str(item.get("mcpc_session") or ""),
            str(item.get("name") or ""),
        ),
    )
    payload = {"catalog": catalog, "tools": canonical_tools}
    return _sha256_json(payload)


def build_tool_catalog_log_entry(
    catalog: CatalogKind,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    tool_records = [
        _tool_record_for_catalog_bundle(tool, catalog=catalog)
        for tool in tools
        if str(tool.get("tool_name") or tool.get("name") or "").strip()
    ]
    content_hash = catalog_bundle_content_hash(catalog, tools)
    return {
        "kind": "tool_catalog",
        "key": f"tool_catalog:{catalog}",
        "catalog": catalog,
        "hash": content_hash,
        "tools": tool_records,
    }


def build_tool_catalog_stub_entry(catalog: CatalogKind, content_hash: str) -> dict[str, Any]:
    return {
        "kind": "tool_catalog",
        "key": f"tool_catalog:{catalog}",
        "catalog": catalog,
        "hash": content_hash,
        "tools": [],
    }


def _catalog_kind_from_source(raw_source: str) -> CatalogKind:
    for kind in _CATALOG_SOURCE_ORDER:
        if raw_source == kind:
            return kind
    return "executor"


def partition_catalog_by_source(
    catalog: list[dict[str, Any]],
) -> dict[CatalogKind, list[dict[str, Any]]]:
    partitions: dict[CatalogKind, list[dict[str, Any]]] = {
        "cyt_mcp": [],
        "mcpc": [],
        "cloudflare": [],
        "executor": [],
        "definitions": [],
    }
    for tool in catalog:
        raw_source = str(tool.get("cyt_catalog_source") or "executor").strip()
        partitions[_catalog_kind_from_source(raw_source)].append(tool)
    return partitions


def catalog_source_order() -> tuple[CatalogKind, ...]:
    return _CATALOG_SOURCE_ORDER


def build_session_state_entry(
    *,
    tools_inject_enabled: bool,
    hallucination_gate_enabled: bool | None = None,
    inject_via: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "session_state",
        "key": "session_state:inject",
        "tools_inject_enabled": tools_inject_enabled,
    }
    if hallucination_gate_enabled is not None:
        entry["hallucination_gate_enabled"] = hallucination_gate_enabled
    if inject_via in {"hook", "proxy"}:
        entry["inject_via"] = inject_via
    return entry


def build_resource_log_entry(match: MatchedResource, *, full: bool) -> dict[str, Any]:
    key = resource_item_key(match)
    entry: dict[str, Any] = {
        "kind": "resource",
        "key": key,
        "hash": resource_content_hash(match),
        "full": full,
        "source": "mcpc",
        "command": match.command.strip(),
        "body": _resource_body(match, full=full),
    }
    if match.name:
        entry["name"] = match.name
    description = match.description.strip()
    if description:
        entry["description"] = description
    return entry


def _tool_dict_from_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": entry.get("name", ""),
        "input_schema": deepcopy(entry.get("input_schema") or {}),
    }
    if entry.get("catalog") == "mcpc":
        tool["tool_name"] = entry.get("name", "")
        tool["mcpc_session"] = entry.get("mcpc_session", "")
        tool["title"] = entry.get("title", tool["name"])
        if "description" in entry:
            tool["description"] = entry["description"]
        server = entry.get("server")
        if isinstance(server, dict):
            tool["server_name"] = server.get("name", "")
            if server.get("instructions"):
                tool["server_instructions"] = server["instructions"]
            if server.get("description"):
                tool["server_description"] = server["description"]
    elif "description" in entry:
        tool["description"] = entry["description"]
    stored_hash = entry.get("hash")
    if isinstance(stored_hash, str) and stored_hash.strip():
        tool["content_hash"] = stored_hash.strip()
    return tool


def _skill_match_from_log_entry(entry: dict[str, Any]) -> MatchedSkill:
    path = str(entry.get("path") or entry.get("command") or "")
    markdown = str(entry.get("body") or "")
    if entry.get("source") == "file" and path and not markdown.startswith("---"):
        markdown = f"---\nname: {entry.get('name', '')}\n---\n\n{markdown}"
    command = entry.get("command")
    stored_hash = entry.get("hash")
    return MatchedSkill(
        doc_id=str(entry.get("key", "")),
        file_path=path,
        markdown=markdown,
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        score=0.0,
        token_count=0,
        command=str(command).strip() if isinstance(command, str) else None,
        content_hash=str(stored_hash).strip()
        if isinstance(stored_hash, str) and stored_hash.strip()
        else None,
    )


def _resource_match_from_log_entry(entry: dict[str, Any]) -> MatchedResource:
    stored_hash = entry.get("hash")
    return MatchedResource(
        doc_id=str(entry.get("key", "")),
        file_path=str(entry.get("path") or ""),
        markdown=str(entry.get("body") or ""),
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        command=str(entry.get("command") or ""),
        description=str(entry.get("description") or ""),
        score=0.0,
        token_count=0,
        content_hash=str(stored_hash).strip()
        if isinstance(stored_hash, str) and stored_hash.strip()
        else None,
    )
