import asyncio
import copy
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastmcp import Client

# Configure logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
OUT = HERE / "catalog"
SCHEMAS_DIR = OUT / "schemas"


def _truncate_description(description: str, max_tokens: int = 60) -> str:
    """
    Truncate description to approximately max_tokens (approx 4 chars per token).
    The plan mentions keep as is, but also refers to truncation.
    We'll do a simple truncation to avoid overly large summaries in vector store.
    """
    if not description:
        return ""

    # Very rough approximation
    max_chars = max_tokens * 4
    if len(description) <= max_chars:
        return description

    return description[:max_chars].rsplit(" ", 1)[0] + "..."


def _get_tool_id(server_name: str, tool_name: str) -> str:
    """Remove server name prefix from tool name if present."""
    prefix = f"{server_name}_"
    if tool_name.startswith(prefix):
        return tool_name[len(prefix) :]
    return tool_name


def _collect_enums(schema: Any, enums: list) -> None:
    """Recursively collect all enum values from a schema tree."""
    if isinstance(schema, dict):
        if "enum" in schema and isinstance(schema["enum"], list):
            enums.extend(schema["enum"])
        for val in schema.values():
            if isinstance(val, dict | list):
                _collect_enums(val, enums)
    elif isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict | list):
                _collect_enums(item, enums)


def _truncate_enum(schema: dict) -> None:
    """Replace enum arrays with >3 distinct values with an empty list."""
    if "enum" in schema and isinstance(schema["enum"], list):
        seen = set()
        distinct = []
        for val in schema["enum"]:
            key = json.dumps(val, sort_keys=True)
            if key not in seen:
                seen.add(key)
                distinct.append(val)
        if len(distinct) > 3:
            schema["enum"] = []


def _process_items(
    result: dict, tool_name: str, server_name: str, path: list, extractions: list
) -> None:
    """Process 'items' field in a schema node."""
    if "items" not in result:
        return
    if isinstance(result["items"], dict):
        result["items"] = _process_node(
            result["items"], tool_name, server_name, [*path, {"type": "items"}], extractions
        )
    elif isinstance(result["items"], list):
        result["items"] = [
            _process_node(
                item,
                tool_name,
                server_name,
                [*path, {"type": "items", "index": i}],
                extractions,
            )
            for i, item in enumerate(result["items"])
        ]


def _process_patterns(
    result: dict, tool_name: str, server_name: str, path: list, extractions: list
) -> None:
    """Process 'patternProperties' field in a schema node."""
    if "patternProperties" not in result or not isinstance(result["patternProperties"], dict):
        return
    for pat, sub in list(result["patternProperties"].items()):
        result["patternProperties"][pat] = _process_node(
            sub,
            tool_name,
            server_name,
            [*path, {"type": "patternProperties", "pattern": pat}],
            extractions,
        )


def _process_compositions(
    result: dict, tool_name: str, server_name: str, path: list, extractions: list
) -> None:
    """Process structural compositions and nested types in a schema node."""
    # Structural compositions (handled like nested objects for recursion)
    for key in ("allOf", "anyOf", "oneOf"):
        if key in result:
            result[key] = [
                _process_node(
                    item, tool_name, server_name, [*path, {"type": key, "index": i}], extractions
                )
                for i, item in enumerate(result[key])
            ]

    for key in ("if", "then", "else"):
        if key in result:
            result[key] = _process_node(
                result[key], tool_name, server_name, [*path, {"type": key}], extractions
            )

    if "not" in result:
        result["not"] = _process_node(
            result["not"], tool_name, server_name, [*path, {"type": "not"}], extractions
        )

    _process_items(result, tool_name, server_name, path, extractions)

    for key in ("contains", "propertyNames", "additionalProperties"):
        if key in result and isinstance(result[key], dict):
            result[key] = _process_node(
                result[key], tool_name, server_name, [*path, {"type": key}], extractions
            )

    _process_patterns(result, tool_name, server_name, path, extractions)


def _process_node(
    node: Any, tool_name: str, server_name: str, path: list, extractions: list
) -> Any:
    """
    Recursively process a schema node.

    Returns the filtered node (optional properties removed, large enums
    truncated).  Populates *extractions* with (path_segments, property_schema)
    tuples for every optional property encountered.
    """
    if not isinstance(node, dict):
        return node

    result = dict(node)
    _process_compositions(result, tool_name, server_name, path, extractions)

    # Property extraction
    if "properties" in result and isinstance(result["properties"], dict):
        raw_required = result.get("required")
        req_props = set(raw_required) if isinstance(raw_required, list) else set()
        filtered_properties = {}

        for prop_name, prop_schema in result["properties"].items():
            is_required = prop_name in req_props
            child_path = [*path, {"type": "properties", "name": prop_name}]

            if is_required:
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
        _truncate_enum(result)
    elif "enum" in result:
        _truncate_enum(result)

    return result


def _build_property_file(tool_name: str, path: list, leaf_schema: dict) -> dict:
    """
    Wrap *leaf_schema* so the JSON tree mirrors the structural path from the
    root ``inputSchema`` down to the extracted property.
    """
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
        else:
            raise ValueError(f"Unknown path segment type: {seg_type}")

    return {"name": tool_name, "inputSchema": current}


def _prepare_tool(server_name: str, tool: Any, all_enums: list, discovered_tools: list) -> None:
    """Prepare tool schema, collect enums, and add to discovered tools."""
    tool_name = tool.name
    tid = f"{server_name}_{tool_name}"

    # Preserve the full schema unchanged for the catalog file
    input_schema = copy.deepcopy(tool.inputSchema)
    full_schema = {
        "name": tool_name,
        "description": tool.description,
        "inputSchema": input_schema,
    }

    _collect_enums(input_schema, all_enums)

    # Write full schema file
    server_full_dir = SCHEMAS_DIR / "full" / server_name
    server_full_dir.mkdir(exist_ok=True)
    full_file = server_full_dir / f"{tool_name}.json"
    full_file.write_text(json.dumps(full_schema, indent=2))

    discovered_tools.append(
        {
            "id": tid,
            "server": server_name,
            "tool": tool_name,
            "summary": _truncate_description(tool.description or ""),
            "full_schema": full_schema,
        }
    )


def _write_enum_files(all_enums: list) -> None:
    """Write unique enums to files."""
    seen = set()
    unique_enums = []
    for val in all_enums:
        key = json.dumps(val, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_enums.append(val)

    unique_enums.sort(key=lambda x: json.dumps(x, sort_keys=True))

    enum_entries = []
    for val in unique_enums:
        if isinstance(val, str):
            filename = f"{val}.json"
            content = val
        else:
            filename = f"{json.dumps(val, sort_keys=True)}.json"
            content = json.dumps(val, sort_keys=True)
        enum_entries.append((SCHEMAS_DIR / "enums" / filename, content))

    enum_entries.sort(key=lambda x: str(x[0]))
    for path, content in enum_entries:
        path.write_text(content)


def _write_tool_and_property_files(discovered_tools: list) -> None:
    """Generate and write tool and property files."""
    tool_entries = []
    property_entries = []

    for tool_info in discovered_tools:
        server_name = tool_info["server"]
        tool_name = tool_info["tool"]
        tool_id = _get_tool_id(server_name, tool_name)
        description = tool_info["full_schema"]["description"]

        input_schema = copy.deepcopy(tool_info["full_schema"]["inputSchema"])
        extractions: list[tuple[list, dict]] = []

        if isinstance(input_schema, dict):
            filtered = _process_node(input_schema, tool_name, server_name, [], extractions)
        else:
            filtered = input_schema

        tool_file = {
            "name": tool_name,
            "description": description,
            "inputSchema": filtered,
        }
        tool_entries.append((SCHEMAS_DIR / "tools" / server_name / f"{tool_id}.json", tool_file))

        for path_segments, prop_schema in extractions:
            prop_name = path_segments[-1]["name"]
            prop_dir = SCHEMAS_DIR / "properties" / server_name / tool_id

            for seg in path_segments[:-1]:
                seg_type = seg["type"]
                if seg_type == "properties":
                    prop_dir = prop_dir / seg["name"]
                elif seg_type == "patternProperties":
                    prop_dir = prop_dir / seg["pattern"]

            property_entries.append((prop_dir / f"{prop_name}.json", prop_schema))

    # Write tool files in lexicographic order
    tool_entries.sort(key=lambda x: str(x[0]))
    for path, tool_content in tool_entries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tool_content, indent=2))

    # Write property files in lexicographic order
    property_entries.sort(key=lambda x: str(x[0]))
    for path, prop_content in property_entries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prop_content, indent=2))


async def build() -> None:
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        print(f"Error: {config_path} not found.")
        return

    try:
        full_config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {config_path}: {e}")
        return

    mcp_servers_config = full_config.get("mcpServers", {})
    if not mcp_servers_config:
        print("No mcpServers found in ~/.claude.json")
        return

    print(f"Connecting to {len(mcp_servers_config)} servers...")

    OUT.mkdir(exist_ok=True)
    if SCHEMAS_DIR.exists():
        shutil.rmtree(SCHEMAS_DIR)

    for sub_dir in ("enums", "tools", "properties", "full"):
        (SCHEMAS_DIR / sub_dir).mkdir(parents=True, exist_ok=True)

    discovered_tools: list[dict] = []
    all_enums: list = []

    for server_name, server_config in mcp_servers_config.items():
        print(f"Connecting to {server_name}...")
        client = Client({"mcpServers": {server_name: server_config}})

        try:
            async with client:
                tools = await client.list_tools()
                print(f"  Discovered {len(tools)} tools on {server_name}.")
                for tool in tools:
                    _prepare_tool(server_name, tool, all_enums, discovered_tools)
        except Exception as e:
            print(f"Warning: Tools might be incomplete for {server_name}: {e}")

    if not discovered_tools:
        print("No tools discovered.")
        return

    _write_enum_files(all_enums)
    _write_tool_and_property_files(discovered_tools)

    (OUT / "tools.json").write_text(json.dumps(discovered_tools, indent=2))
    print(f"Successfully wrote {len(discovered_tools)} tools to {OUT / 'tools.json'}")


if __name__ == "__main__":
    asyncio.run(build())
