"""Persist cyt-mcp search results and conversation turns to session JSONL (stdlib only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from cyt_client.agent import infer_harness_agent
from cyt_client.sessions import (
    append_tool_catalog_entries,
    read_latest_tool_catalogs,
    session_log_path,
)
from cyt_client.tool_gate import normalize_mcp_tool_name
from cyt_client.transcript import last_assistant_from_payload, prompt_from_payload

SEARCH_TOOL_NAME = "cyt-mcp_get-tool-definitions"
_TOOL_DEF_HASH_PREFIX = b"v1-tool-def\x00"

_POST_TOOL_EVENTS = frozenset(
    {
        "PostToolUse",
        "postToolUse",
    },
)

_PROMPT_SUBMIT_EVENTS = frozenset(
    {
        "beforeSubmitPrompt",
        "UserPromptSubmit",
    },
)


def is_prompt_submit_event(payload: dict[str, Any]) -> bool:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        event = layer.get("hook_event_name") or layer.get("hookEventName")
        if isinstance(event, str) and event.strip() in _PROMPT_SUBMIT_EVENTS:
            return True
    return False


def is_post_tool_capture_event(payload: dict[str, Any]) -> bool:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        event = layer.get("hook_event_name") or layer.get("hookEventName")
        if isinstance(event, str) and event.strip() in _POST_TOOL_EVENTS:
            return True
    return False


def _payload_layers(data: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [data]
    nested = data.get("payload")
    if isinstance(nested, dict):
        layers.append(nested)
    return layers


def _first_value(data: dict[str, Any], *keys: str) -> object | None:
    for layer in _payload_layers(data):
        for key in keys:
            if key in layer:
                return cast(object, layer[key])
    return None


def _parse_json_object(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            block = cast(dict[str, Any], first)
            text = block.get("text")
            if isinstance(text, str):
                return _parse_json_object(text)
    return None


def _definition_from_result_body(body: object) -> dict[str, Any] | None:
    parsed = _parse_json_object(body)
    if parsed is None:
        return None
    schema = parsed.get("inputSchema") or parsed.get("input_schema")
    name = parsed.get("name")
    if isinstance(name, str) and name.strip() and isinstance(schema, dict):
        return parsed
    content = parsed.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            nested = _parse_json_object(block.get("text") or block.get("json"))
            if nested is not None:
                return nested
    return None


def extract_cyt_mcp_search_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    agent = infer_harness_agent(payload)
    outer_name = _first_value(payload, "tool_name", "toolName", "tool", "name")
    if not isinstance(outer_name, str):
        return None
    normalized_outer = normalize_mcp_tool_name(outer_name, agent=agent)
    if normalized_outer != SEARCH_TOOL_NAME:
        return None

    args = _first_value(payload, "tool_input", "toolInput", "arguments", "args", "input")
    if not isinstance(args, dict):
        args = _parse_json_object(args) or {}
    args_dict = cast(dict[str, Any], args)
    nested_name = args_dict.get("tool_name") or args_dict.get("toolName")
    if not isinstance(nested_name, str) or not nested_name.strip():
        return None

    result_body = _first_value(
        payload,
        "tool_result",
        "toolResult",
        "tool_output",
        "toolOutput",
        "result_json",
        "resultJson",
        "tool_response",
        "toolResponse",
        "result",
        "output",
        "content",
    )
    definition = _definition_from_result_body(result_body)
    if definition is None:
        return None
    return nested_name.strip(), definition


def _tool_definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_TOOL_DEF_HASH_PREFIX + canonical.encode("utf-8")).hexdigest()


def _tool_record_for_catalog(tool_name: str, definition: dict[str, Any]) -> dict[str, Any]:
    input_schema = definition.get("inputSchema") or definition.get("input_schema") or {}
    if not isinstance(input_schema, dict):
        input_schema = {}
    record: dict[str, Any] = {
        "name": tool_name,
        "input_schema": input_schema,
    }
    if definition.get("description") is not None:
        record["description"] = str(definition["description"])
    return record


def _catalog_bundle_content_hash(tools: list[dict[str, Any]]) -> str:
    canonical_tools = sorted(
        tools,
        key=lambda item: str(item.get("name") or ""),
    )
    payload = {"catalog": "cyt_mcp", "tools": canonical_tools}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_cyt_mcp_catalog_entry(tools: list[dict[str, Any]]) -> dict[str, Any]:
    content_hash = _catalog_bundle_content_hash(tools)
    return {
        "kind": "tool_catalog",
        "key": "tool_catalog:cyt_mcp",
        "catalog": "cyt_mcp",
        "hash": content_hash,
        "tools": canonical_tools_sorted(tools),
    }


def canonical_tools_sorted(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tools, key=lambda item: str(item.get("name") or ""))


def merge_tool_into_cyt_mcp_catalog(
    path: Path,
    tool_name: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    tool_record = _tool_record_for_catalog(tool_name, definition)
    catalogs = read_latest_tool_catalogs(path)
    existing = catalogs.get("tool_catalog:cyt_mcp")
    tools: list[dict[str, Any]] = []
    if existing is not None:
        raw_tools = existing.get("tools")
        if isinstance(raw_tools, list):
            tools = [dict(item) for item in raw_tools if isinstance(item, dict)]
    replaced = False
    for index, item in enumerate(tools):
        if str(item.get("name") or "").strip() == tool_name:
            tools[index] = tool_record
            replaced = True
            break
    if not replaced:
        tools.append(tool_record)
    return build_cyt_mcp_catalog_entry(tools)


def persist_cyt_mcp_search_result(payload: dict[str, Any]) -> bool:
    extracted = extract_cyt_mcp_search_result(payload)
    if extracted is None:
        return False
    tool_name, definition = extracted
    path = session_log_path(payload)
    if path is None:
        return False
    entry = merge_tool_into_cyt_mcp_catalog(path, tool_name, definition)
    catalogs = read_latest_tool_catalogs(path)
    existing = catalogs.get("tool_catalog:cyt_mcp")
    if existing is not None and str(existing.get("hash") or "") == str(entry.get("hash") or ""):
        return False
    agent = infer_harness_agent(payload)
    append_tool_catalog_entries(path, [entry], agent=agent)
    return True


def build_turn_entry(
    prompt: str,
    assistant: str,
    *,
    turn_id: str | None = None,
) -> dict[str, Any]:
    prompt_text = prompt.strip()
    key = (
        f"turn:{turn_id.strip()}"
        if turn_id and turn_id.strip()
        else (f"turn:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}")
    )
    return {
        "kind": "turn",
        "key": key,
        "prompt": prompt_text,
        "assistant": assistant.strip(),
    }


def persist_turn_to_session_log(payload: dict[str, Any]) -> bool:
    prompt = prompt_from_payload(payload)
    if not prompt:
        return False
    assistant = last_assistant_from_payload(payload) or ""
    turn_id = _first_value(payload, "turn_id", "turnId")
    turn_id_str = turn_id.strip() if isinstance(turn_id, str) else None
    entry = build_turn_entry(prompt, assistant, turn_id=turn_id_str)
    path = session_log_path(payload)
    if path is None:
        return False
    from cyt_client.sessions import append_session_log

    if _session_has_turn_key(path, entry["key"]):
        return False
    agent = infer_harness_agent(payload)
    append_session_log(path, [entry], agent=agent)
    return True


def _session_has_turn_key(path: Path, key: str) -> bool:
    from cyt_client.sessions import read_session_log_file

    _agent, entries = read_session_log_file(path)
    return any(str(entry.get("key") or "") == key for entry in entries)
