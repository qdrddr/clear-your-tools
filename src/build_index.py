import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

_TIKTOKEN_ENCODING: tiktoken.Encoding | None = None


def _tiktoken_encoding() -> tiktoken.Encoding:
    global _TIKTOKEN_ENCODING
    if _TIKTOKEN_ENCODING is None:
        _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
    return _TIKTOKEN_ENCODING


def compact_json(obj: Any) -> str:
    """Serialize JSON without indentation (stable token accounting)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def count_tokens(text: str) -> int:
    """Return tiktoken count for text under cl100k_base."""
    return len(_tiktoken_encoding().encode(text))


def count_json_tokens(obj: Any) -> int:
    """Compact-serialize obj and return its token count."""
    return count_tokens(compact_json(obj))


def log_token_usage(label: str, tokens: int) -> None:
    """Print and log a token count line (console + logger)."""
    msg = f"{label}: {tokens} tokens"
    logger.info(msg)
    print(msg, flush=True)

JSON_EXT = ".json"
MD_EXT = ".md"
DECOMPOSED_PREFIX = "schemas/decomposed/"


def tool_id_from_decomposed_rel(rel_path: str) -> str:
    """Return the tool id encoded in a decomposed catalog relative path."""
    if rel_path.startswith(DECOMPOSED_PREFIX):
        rel = rel_path[len(DECOMPOSED_PREFIX) :]
    else:
        rel = rel_path
    parts = Path(rel).parts
    if not parts:
        return Path(rel).stem
    first = parts[0]
    if first.endswith(JSON_EXT):
        return first[: -len(JSON_EXT)]
    return first


# ------------------------------------------------------------------ #
# Data types
# ------------------------------------------------------------------ #
@dataclass
class CatalogIndex:
    """In-memory catalog index: tool metadata plus generated file contents."""

    tools: list[dict[str, Any]]
    files: dict[str, str] = field(default_factory=dict)

    def to_catalog_dict(self, catalog_prefix: str = "src/catalog") -> dict[str, list[dict[str, Any]]]:
        """
        Convert decomposed catalog files to the dict format used by cs.py, rerank.py, and llm.py.

        Only files under schemas/decomposed/ are included (enums as md, tool schemas as json).
        """
        md_entries: list[dict[str, Any]] = []
        json_entries: list[dict[str, Any]] = []

        for rel_path, content in sorted(self.files.items()):
            if not rel_path.startswith(DECOMPOSED_PREFIX):
                continue

            file_path = f"{catalog_prefix}/{rel_path}"
            suffix = Path(rel_path).suffix.lower()

            if suffix == MD_EXT:
                md_entries.append(
                    {
                        "id": Path(rel_path).stem,
                        "file_path": file_path,
                        "score": 1.0,
                        "start_line": 1,
                        "end_line": 1,
                        "language": "markdown",
                        "content": content,
                    },
                )
            elif suffix == JSON_EXT:
                parsed = json.loads(content)
                line_count = len(content.splitlines())
                entry_id = parsed.get("id") or tool_id_from_decomposed_rel(rel_path)
                json_entries.append(
                    {
                        "id": entry_id,
                        "name": entry_id,
                        "file_path": file_path,
                        "score": 1.0,
                        "start_line": 1,
                        "end_line": line_count,
                        "language": "json",
                        "content": parsed,
                    },
                )

        return {"md": md_entries, "json": json_entries, "tools": self.tools}

    def pruned_tools_from_reranked(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Rebuild merged tool schemas from a reranked catalog dict (in-memory only)."""
        from retrieve_catalog import retrieve_tools

        return retrieve_tools(
            data,
            catalog=self,
            apply_decomposed_score_filter=False,
        )


# ------------------------------------------------------------------ #
# Schema processing helpers
# ------------------------------------------------------------------ #
def truncate_description(description: str | None, max_tokens: int = 60) -> str:
    if not description:
        return ""
    max_chars = max_tokens * 4
    if len(description) <= max_chars:
        return description
    return description[:max_chars].rsplit(" ", 1)[0] + "..."


def collect_enums(schema: Any) -> list[Any]:
    """Walk a JSON schema and return all enum values found."""
    found: list[Any] = []
    if isinstance(schema, dict):
        if "enum" in schema and isinstance(schema["enum"], list):
            found.extend(schema["enum"])
        for val in schema.values():
            if isinstance(val, dict | list):
                found.extend(collect_enums(val))
    elif isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict | list):
                found.extend(collect_enums(item))
    return found


def dedupe_enums(all_enums: list[Any]) -> list[Any]:
    seen: set[str] = set()
    unique_enums: list[Any] = []
    for val in all_enums:
        key = json.dumps(val, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_enums.append(val)
    unique_enums.sort(key=lambda x: json.dumps(x, sort_keys=True))
    return unique_enums


def _build_property_file(
    tool_name: str, path: list[dict[str, Any]], leaf_schema: Any
) -> dict[str, Any]:
    current = leaf_schema
    for segment in reversed(path):
        seg_type = segment["type"]
        if seg_type == "properties":
            current = {"properties": {segment["name"]: current}}
        elif seg_type == "items":
            if "index" in segment:
                current = {"items": [current]}
            else:
                current = {"items": current}
        elif seg_type in ("allOf", "anyOf", "oneOf"):
            current = {seg_type: [current]}
        elif seg_type == "additionalProperties":
            current = {"additionalProperties": current}
        elif seg_type == "patternProperties":
            current = {"patternProperties": {segment["pattern"]: current}}
        elif seg_type in ("if", "then", "else", "not", "contains", "propertyNames"):
            current = {seg_type: current}
    return {"id": tool_name, "name": tool_name, "inputSchema": current}


def _process_node(
    node: Any,
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> Any:
    if not isinstance(node, dict):
        return node
    result = dict(node)
    _process_compositions(result, tool_name, server_name, path, extractions)
    if "properties" in result and isinstance(result["properties"], dict):
        raw_req = result.get("required")
        req_props = set(raw_req) if isinstance(raw_req, list) else set()
        filtered_properties = {}
        for prop_name, prop_schema in result["properties"].items():
            child_path = [*path, {"type": "properties", "name": prop_name}]
            if prop_name in req_props:
                filtered_properties[prop_name] = _process_node(
                    prop_schema, tool_name, server_name, child_path, extractions
                )
            else:
                filtered_child = _process_node(
                    prop_schema, tool_name, server_name, child_path, extractions
                )
                prop_file = _build_property_file(tool_name, child_path, filtered_child)
                extractions.append((child_path, prop_file))
        result["properties"] = filtered_properties
    return result


def _process_compositions(
    result: dict[str, Any],
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    _handle_logical_compositions(result, tool_name, server_name, path, extractions)
    _handle_conditional_compositions(result, tool_name, server_name, path, extractions)
    _handle_array_properties(result, tool_name, server_name, path, extractions)
    _handle_miscellaneous_keywords(result, tool_name, server_name, path, extractions)


def _handle_logical_compositions(
    result: dict[str, Any],
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    for key in ("allOf", "anyOf", "oneOf"):
        if key in result and isinstance(result[key], list):
            result[key] = [
                _process_node(
                    item, tool_name, server_name, [*path, {"type": key, "index": i}], extractions
                )
                for i, item in enumerate(result[key])
            ]


def _handle_conditional_compositions(
    result: dict[str, Any],
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    for key in ("if", "then", "else"):
        if key in result:
            result[key] = _process_node(
                result[key], tool_name, server_name, [*path, {"type": key}], extractions
            )
    if "not" in result:
        result["not"] = _process_node(
            result["not"], tool_name, server_name, [*path, {"type": "not"}], extractions
        )


def _handle_array_properties(
    result: dict[str, Any],
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    if "items" in result:
        if isinstance(result["items"], dict):
            result["items"] = _process_node(
                result["items"], tool_name, server_name, [*path, {"type": "items"}], extractions
            )
        elif isinstance(result["items"], list):
            result["items"] = [
                _process_node(
                    item, tool_name, server_name, [*path, {"type": "items", "index": i}], extractions
                )
                for i, item in enumerate(result["items"])
            ]


def _handle_miscellaneous_keywords(
    result: dict[str, Any],
    tool_name: str,
    server_name: str,
    path: list[dict[str, Any]],
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    for key in ("contains", "propertyNames", "additionalProperties"):
        if key in result and isinstance(result[key], dict):
            result[key] = _process_node(
                result[key], tool_name, server_name, [*path, {"type": key}], extractions
            )
    if "patternProperties" in result and isinstance(result["patternProperties"], dict):
        for pat, sub in result["patternProperties"].items():
            result["patternProperties"][pat] = _process_node(
                sub, tool_name, server_name, [*path, {"type": "patternProperties", "pattern": pat}], extractions
            )


def decompose_tool_schema(
    tool_info: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[list[dict[str, Any]], dict[str, Any]]]]:
    """Decompose one tool schema into a filtered root schema and extracted property files."""
    tool_id: str = tool_info["id"]
    t_desc: str = tool_info["full_schema"]["description"]
    t_schema: Any = copy.deepcopy(tool_info["full_schema"]["inputSchema"])
    extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    filtered = (
        _process_node(t_schema, tool_id, tool_info.get("server", ""), [], extractions)
        if isinstance(t_schema, dict)
        else t_schema
    )
    root_schema = {"id": tool_id, "name": tool_id, "description": t_desc, "inputSchema": filtered}
    return root_schema, extractions


def _property_relative_path(
    tool_id: str,
    path_segments: list[dict[str, Any]],
    prop_name: str,
) -> str:
    parts = [DECOMPOSED_PREFIX.rstrip("/"), tool_id]
    for seg in path_segments[:-1]:
        if seg["type"] == "properties":
            parts.append(seg["name"])
        elif seg["type"] == "patternProperties":
            parts.append(seg["pattern"])
    parts.append(f"{prop_name}.json")
    return "/".join(parts)


def prepare_tool_entry(server_name: str, tool: Any) -> dict[str, Any]:
    """Build one non-system (mcp__ id) or generic tool catalog entry without file I/O."""
    tool_id: str = tool.name

    input_schema = copy.deepcopy(tool.inputSchema)
    full_schema = {
        "id": tool_id,
        "name": tool_id,
        "description": tool.description,
        "inputSchema": input_schema,
    }

    return {
        "id": tool_id,
        "server": server_name,
        "tool": tool_id,
        "summary": truncate_description(tool.description or ""),
        "full_schema": full_schema,
    }


def prepare_system_tool_entry(tool: Any) -> dict[str, Any]:
    """Build a system tool entry (id does not use mcp__ prefix)."""
    return prepare_tool_entry("", tool)


def build_catalog_index(
    tools: list[dict[str, Any]],
    all_enums: list[Any],
) -> CatalogIndex:
    """Build the full catalog index in memory without writing to disk."""
    files: dict[str, str] = {}

    for tool_info in tools:
        tool_id: str = tool_info["id"]
        full_schema = tool_info["full_schema"]
        files[f"schemas/full/{tool_id}.json"] = json.dumps(full_schema, indent=2)

    for val in dedupe_enums(all_enums):
        files[f"{DECOMPOSED_PREFIX}{val}.md"] = str(val)

    for tool_info in tools:
        tool_id = tool_info["id"]
        root_schema, extractions = decompose_tool_schema(tool_info)

        files[f"{DECOMPOSED_PREFIX}{tool_id}.json"] = json.dumps(root_schema, indent=2)

        for path_segments, prop_schema in extractions:
            prop_name: str = path_segments[-1]["name"]
            rel_path = _property_relative_path(tool_id, path_segments, prop_name)
            files[rel_path] = json.dumps(prop_schema, indent=2)

    files["tools.json"] = json.dumps(tools, indent=2)
    return CatalogIndex(tools=tools, files=files)
