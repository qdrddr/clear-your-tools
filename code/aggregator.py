import argparse
import asyncio
import copy
import json
import logging
import types
from pathlib import Path
from typing import Any, Literal, cast

from fastmcp import Client, FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
OUT = HERE / "catalog"
SCHEMAS_DIR = OUT / "schemas"


class MCPAggregator:
    def __init__(self) -> None:
        self.mcp = FastMCP("MCP Aggregator")
        self.clients: dict[str, Client] = {}
        self.tool_mapping: dict[
            str,
            tuple[str, str],
        ] = {}  # frontend_name -> (server_name, backend_name)
        self.output_map: dict[Path, str] = {}
        self.discovered_tools: list[dict[str, Any]] = []
        self.all_enums: list[Any] = []

    def _smart_write(self, path: Path, content: str) -> None:
        """Collect output in memory for later idempotent writing."""
        self.output_map[path.absolute()] = content

    def _apply_outputs(self) -> None:
        """Idempotently write all collected files to disk."""
        for path, content in self.output_map.items():
            if path.exists():
                try:
                    if path.read_text() == content:
                        continue
                except Exception:
                    pass
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def _prune_stale_files(self, root: Path, expected_paths: set[Path]) -> None:
        """Remove files in root that are not in expected_paths, and empty dirs."""
        if not root.exists():
            return
        for path in root.rglob("*"):
            if path.is_file() and path.absolute() not in expected_paths:
                path.unlink()
        for path in sorted(root.rglob("*"), key=lambda x: len(str(x)), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    def _truncate_description(self, description: str | None, max_tokens: int = 60) -> str:
        if not description:
            return ""
        max_chars = max_tokens * 4
        if len(description) <= max_chars:
            return description
        return description[:max_chars].rsplit(" ", 1)[0] + "..."

    def _get_tool_id(self, server_name: str, tool_name: str) -> str:
        prefix = f"{server_name}_"
        if tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
        return tool_name

    def _collect_enums(self, schema: Any) -> None:
        if isinstance(schema, dict):
            if "enum" in schema and isinstance(schema["enum"], list):
                self.all_enums.extend(schema["enum"])
            for val in schema.values():
                if isinstance(val, dict | list):
                    self._collect_enums(val)
        elif isinstance(schema, list):
            for item in schema:
                if isinstance(item, dict | list):
                    self._collect_enums(item)

    def _process_node(
        self,
        node: Any,
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> Any:
        if not isinstance(node, dict):
            return node
        result = dict(node)
        self._process_compositions(result, tool_name, server_name, path, extractions)
        if "properties" in result and isinstance(result["properties"], dict):
            raw_req = result.get("required")
            req_props = set(raw_req) if isinstance(raw_req, list) else set()
            filtered_properties = {}
            for prop_name, prop_schema in result["properties"].items():
                child_path = [*path, {"type": "properties", "name": prop_name}]
                if prop_name in req_props:
                    filtered_properties[prop_name] = self._process_node(
                        prop_schema,
                        tool_name,
                        server_name,
                        child_path,
                        extractions,
                    )
                else:
                    filtered_child = self._process_node(
                        prop_schema,
                        tool_name,
                        server_name,
                        child_path,
                        extractions,
                    )
                    prop_file = self._build_property_file(tool_name, child_path, filtered_child)
                    extractions.append((child_path, prop_file))
            result["properties"] = filtered_properties
        return result

    def _process_compositions(
        self,
        result: dict[str, Any],
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        self._handle_logical_compositions(result, tool_name, server_name, path, extractions)
        self._handle_conditional_compositions(result, tool_name, server_name, path, extractions)
        self._handle_array_properties(result, tool_name, server_name, path, extractions)
        self._handle_miscellaneous_keywords(result, tool_name, server_name, path, extractions)

    def _handle_logical_compositions(
        self,
        result: dict[str, Any],
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        for key in ("allOf", "anyOf", "oneOf"):
            if key in result and isinstance(result[key], list):
                result[key] = [
                    self._process_node(
                        item,
                        tool_name,
                        server_name,
                        [*path, {"type": key, "index": i}],
                        extractions,
                    )
                    for i, item in enumerate(result[key])
                ]

    def _handle_conditional_compositions(
        self,
        result: dict[str, Any],
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        for key in ("if", "then", "else"):
            if key in result:
                result[key] = self._process_node(
                    result[key],
                    tool_name,
                    server_name,
                    [*path, {"type": key}],
                    extractions,
                )
        if "not" in result:
            result["not"] = self._process_node(
                result["not"],
                tool_name,
                server_name,
                [*path, {"type": "not"}],
                extractions,
            )

    def _handle_array_properties(
        self,
        result: dict[str, Any],
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        if "items" in result:
            if isinstance(result["items"], dict):
                result["items"] = self._process_node(
                    result["items"],
                    tool_name,
                    server_name,
                    [*path, {"type": "items"}],
                    extractions,
                )
            elif isinstance(result["items"], list):
                result["items"] = [
                    self._process_node(
                        item,
                        tool_name,
                        server_name,
                        [*path, {"type": "items", "index": i}],
                        extractions,
                    )
                    for i, item in enumerate(result["items"])
                ]

    def _handle_miscellaneous_keywords(
        self,
        result: dict[str, Any],
        tool_name: str,
        server_name: str,
        path: list[dict[str, Any]],
        extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        for key in ("contains", "propertyNames", "additionalProperties"):
            if key in result and isinstance(result[key], dict):
                result[key] = self._process_node(
                    result[key],
                    tool_name,
                    server_name,
                    [*path, {"type": key}],
                    extractions,
                )
        if "patternProperties" in result and isinstance(result["patternProperties"], dict):
            for pat, sub in result["patternProperties"].items():
                result["patternProperties"][pat] = self._process_node(
                    sub,
                    tool_name,
                    server_name,
                    [*path, {"type": "patternProperties", "pattern": pat}],
                    extractions,
                )

    def _build_property_file(
        self,
        tool_name: str,
        path: list[dict[str, Any]],
        leaf_schema: Any,
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
        return {"name": tool_name, "inputSchema": current}

    def _prepare_tool(self, server_name: str, tool: Any) -> None:
        tool_name: str = tool.name
        prefix = f"{server_name}_"
        frontend_name = tool_name if tool_name.startswith(prefix) else f"{server_name}_{tool_name}"

        self.tool_mapping[frontend_name] = (server_name, tool_name)

        tid = frontend_name
        input_schema = copy.deepcopy(tool.inputSchema)
        full_schema = {
            "name": tool_name,
            "description": tool.description,
            "inputSchema": input_schema,
        }
        self._collect_enums(input_schema)

        full_file = SCHEMAS_DIR / "full" / server_name / f"{tool_name}.json"
        self._smart_write(full_file, json.dumps(full_schema, indent=2))

        self.discovered_tools.append(
            {
                "id": tid,
                "server": server_name,
                "tool": tool_name,
                "summary": self._truncate_description(tool.description or ""),
                "full_schema": full_schema,
            },
        )

    def _write_catalog_files(self) -> None:
        # Enums
        seen: set[str] = set()
        unique_enums: list[Any] = []
        for val in self.all_enums:
            key = json.dumps(val, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_enums.append(val)
        unique_enums.sort(key=lambda x: json.dumps(x, sort_keys=True))
        for val in unique_enums:
            self._smart_write(SCHEMAS_DIR / "decomposed" / f"{val}.md", str(val))

        # Tools and properties
        for tool_info in self.discovered_tools:
            s_name: str = tool_info["server"]
            t_name: str = tool_info["tool"]
            t_id = self._get_tool_id(s_name, t_name)
            t_desc: str = tool_info["full_schema"]["description"]
            t_schema: Any = copy.deepcopy(tool_info["full_schema"]["inputSchema"])
            extractions: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
            filtered = (
                self._process_node(t_schema, t_name, s_name, [], extractions)
                if isinstance(t_schema, dict)
                else t_schema
            )

            self._smart_write(
                SCHEMAS_DIR / "decomposed" / s_name / f"{t_id}.json",
                json.dumps(
                    {"name": t_name, "description": t_desc, "inputSchema": filtered},
                    indent=2,
                ),
            )

            for path_segments, prop_schema in extractions:
                prop_name: str = path_segments[-1]["name"]
                prop_dir = SCHEMAS_DIR / "decomposed" / s_name / t_id
                for seg in path_segments[:-1]:
                    if seg["type"] == "properties":
                        prop_dir = prop_dir / seg["name"]
                    elif seg["type"] == "patternProperties":
                        prop_dir = prop_dir / seg["pattern"]
                self._smart_write(prop_dir / f"{prop_name}.json", json.dumps(prop_schema, indent=2))

        self._smart_write(OUT / "tools.json", json.dumps(self.discovered_tools, indent=2))
        self._apply_outputs()
        self._prune_stale_files(OUT, set(self.output_map.keys()))

    def _get_mcp_config(self) -> dict[str, dict[str, Any]]:
        config_path = Path.home() / ".claude.json"
        if not config_path.exists():
            logger.error("~/.claude.json not found")
            return {}
        try:
            data = json.loads(config_path.read_text())
            if not isinstance(data, dict):
                return {}
            servers = data.get("mcpServers", {})
            if not isinstance(servers, dict):
                return {}
            return servers
        except Exception:
            logger.exception("Error reading config")
            return {}

    def _is_self_recursion(
        self,
        s_name: str,
        s_config: dict[str, Any],
        current_script: Path,
    ) -> bool:
        if s_name == self.mcp.name:
            logger.warning("Skipping server '%s' - matches aggregator name", s_name)
            return True

        cmd: str | None = s_config.get("command")
        args_raw: list[str] | str = s_config.get("args", [])
        parts = [cmd] + (args_raw if isinstance(args_raw, list) else [])
        for part in parts:
            if not part or not isinstance(part, str):
                continue
            try:
                p = Path(part)
                if p.is_file() and p.resolve() == current_script:
                    return True
                if p.is_dir() and p.resolve() == current_script.parent and "aggregator" in part:
                    return True
            except Exception:
                continue
        return False

    async def _connect_to_server(self, s_name: str, s_config: dict[str, Any]) -> None:
        logger.info("Connecting to %s...", s_name)
        client = Client({"mcpServers": {s_name: s_config}})
        try:
            await client.__aenter__()
            remote_info = client.initialize_result
            if remote_info and remote_info.serverInfo.name == self.mcp.name:
                logger.warning("Skipping server '%s' - name matches: '%s'", s_name, self.mcp.name)
                await client.__aexit__(None, None, None)
                return

            self.clients[s_name] = client
            tools = await client.list_tools()
            logger.info("  Discovered %d tools on %s", len(tools), s_name)
            for tool in tools:
                self._prepare_tool(s_name, tool)
        except Exception:
            logger.exception("Failed to connect to %s", s_name)

    async def initialize(self) -> None:
        config = self._get_mcp_config()
        if not config:
            return

        OUT.mkdir(exist_ok=True, parents=True)
        SCHEMAS_DIR.mkdir(exist_ok=True, parents=True)

        current_script = Path(__file__).resolve()
        for s_name, s_config in config.items():
            if self._is_self_recursion(s_name, s_config, current_script):
                continue

            await self._connect_to_server(s_name, s_config)

        self._write_catalog_files()
        self._register_tools()

    def _register_tools(self) -> None:
        for f_name, (sn, bn) in self.tool_mapping.items():
            tool_info = next(
                (t for t in self.discovered_tools if t["server"] == sn and t["tool"] == bn),
                None,
            )
            if not tool_info:
                continue

            desc: str = tool_info["full_schema"].get("description", "")
            schema: dict[str, Any] = tool_info["full_schema"].get("inputSchema", {})

            def make_handler(sn_val: str, bn_val: str, schema_val: dict[str, Any]) -> Any:
                props: dict[str, Any] = schema_val if isinstance(schema_val, dict) else {}
                props = props.get("properties", {})
                arg_names = [name for name in props.keys() if name.isidentifier()]
                args_str = ", ".join(arg_names)

                code_lines = [
                    f"async def handler(self, {args_str}):",
                    "    params = {" + ", ".join([f"'{name}': {name}" for name in arg_names]) + "}",
                    f'    client = self.clients.get("{sn_val}")',
                    "    if not client:",
                    f'        return "Error: Backend server {sn_val} is not connected"',
                    "    try:",
                    f'        return await client.call_tool("{bn_val}", params)',
                    "    except Exception as e:",
                    f'        return f"Error calling {bn_val} on {sn_val}: {{str(e)}}"',
                ]
                code = "\n".join(code_lines)
                loc: dict[str, Any] = {}
                exec(code, globals(), loc)
                return loc["handler"]

            try:
                handler = make_handler(sn, bn, schema)
                bound_handler = types.MethodType(handler, self)
                self.mcp.tool(name=f_name, description=desc)(bound_handler)
            except Exception:
                logger.exception("Failed to register tool %s", f_name)

    async def run(
        self,
        transport: Literal["stdio", "http", "sse", "streamable-http"] = "http",
    ) -> None:
        try:
            await self.initialize()
            logger.info("Starting SCA on %s transport...", transport)
            await self.mcp.run_async(transport=transport)
        finally:
            for client in self.clients.values():
                try:
                    await client.__aexit__(None, None, None)
                except Exception:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Aggregator")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    cmd_args = parser.parse_args()
    aggregator = MCPAggregator()
    asyncio.run(
        aggregator.run(
            transport=cast(Literal["stdio", "http", "sse", "streamable-http"], cmd_args.transport),
        ),
    )
