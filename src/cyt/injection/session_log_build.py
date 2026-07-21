"""Build native JSON session-log records from injected tool/skill/resource items."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from cyt.common.paths import shorten_home_path
from cyt.mcpc.catalog_disk import _canonical_tool_entry
from cyt.resources.inject import MatchedResource
from cyt.skills.catalog import content_sha256_for_file
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import format_tool_item
from cyt.tools.mcpc_inject import _format_mcpc_tool_item, _mcpc_injection_schema_body

CatalogKind = Literal["executor", "mcpc"]
ItemKind = Literal["tool", "skill", "resource"]


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tool_content_hash(tool: dict[str, Any], *, catalog: CatalogKind) -> str:
    if catalog == "mcpc":
        return _sha256_json(_canonical_tool_entry(tool))
    schema = tool.get("input_schema") or tool.get("parameters") or tool.get("inputSchema")
    definition: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or ""),
        "input_schema": schema if isinstance(schema, dict) else {},
    }
    return _sha256_json(definition)


def skill_content_hash(match: MatchedSkill) -> str:
    path = match.file_path.strip()
    if path:
        try:
            return content_sha256_for_file(Path(path))
        except OSError:
            pass
    return hashlib.sha256(match.markdown.encode("utf-8")).hexdigest()


def resource_content_hash(match: MatchedResource) -> str:
    path = match.file_path.strip()
    if path:
        try:
            return content_sha256_for_file(Path(path))
        except OSError:
            pass
    return hashlib.sha256(match.markdown.encode("utf-8")).hexdigest()


def tool_item_key(tool: dict[str, Any], *, catalog: CatalogKind) -> str:
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    if catalog == "mcpc":
        session = str(tool.get("mcpc_session") or "").strip()
        return f"tool:{session}:{name}"
    return f"tool:{name}"


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
        catalog: CatalogKind = "mcpc" if raw_catalog == "mcpc" else "executor"
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
) -> dict[str, Any]:
    key = tool_item_key(tool, catalog=catalog)
    content_hash = tool_content_hash(tool, catalog=catalog)
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
    return tool


def _skill_match_from_log_entry(entry: dict[str, Any]) -> MatchedSkill:
    path = str(entry.get("path") or entry.get("command") or "")
    markdown = str(entry.get("body") or "")
    if entry.get("source") == "file" and path and not markdown.startswith("---"):
        markdown = f"---\nname: {entry.get('name', '')}\n---\n\n{markdown}"
    command = entry.get("command")
    return MatchedSkill(
        doc_id=str(entry.get("key", "")),
        file_path=path,
        markdown=markdown,
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        score=0.0,
        token_count=0,
        command=str(command).strip() if isinstance(command, str) else None,
    )


def _resource_match_from_log_entry(entry: dict[str, Any]) -> MatchedResource:
    return MatchedResource(
        doc_id=str(entry.get("key", "")),
        file_path=str(entry.get("path") or ""),
        markdown=str(entry.get("body") or ""),
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        command=str(entry.get("command") or ""),
        description=str(entry.get("description") or ""),
        score=0.0,
        token_count=0,
    )
