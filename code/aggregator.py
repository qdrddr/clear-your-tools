import argparse
import asyncio
import copy
import json
import logging
import inspect
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
OUT = HERE / "catalog"
SCHEMAS_DIR = OUT / "schemas"


class MCPAggregator:
    def __init__(self):
        self.mcp = FastMCP("MCP Aggregator")
        self.clients: dict[str, Client] = {}
        self.tool_mapping: dict[str, tuple[str, str]] = {}  # frontend_name -> (server_name, backend_name)
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
        for path in list(root.rglob("*")):
            if path.is_file() and path.absolute() not in expected_paths:
                path.unlink()
        for path in sorted(root.rglob("*"), key=lambda x: len(str(x)), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    def _truncate_description(self, description: str, max_tokens: int = 60) -> str:
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
                if isinstance(val, (dict, list)):
                    self._collect_enums(val)
        elif isinstance(schema, list):
            for item in schema:
                if isinstance(item, (dict, list)):
                    self._collect_enums(item)

    def _process_node(self, node: Any, tool_name: str, server_name: str, path: list, extractions: list) -> Any:
        if not isinstance(node, dict):
            return node
        result = dict(node)
        self._process_compositions(result, tool_name, server_name, path, extractions)
        if "properties" in result and isinstance(result["properties"], dict):
            raw_required = result.get("required")
            req_props = set(raw_required) if isinstance(raw_required, list) else set()
            filtered_properties = {}
            for prop_name, prop_schema in result["properties"].items():
                child_path = [*path, {"type": "properties", "name": prop_name}]
                if prop_name in req_props:
                    filtered_properties[prop_name] = self._process_node(prop_schema, tool_name, server_name, child_path, extractions)
                else:
                    filtered_child = self._process_node(prop_schema, tool_name, server_name, child_path, extractions)
                    prop_file = self._build_property_file(tool_name, child_path, filtered_child)
                    extractions.append((child_path, prop_file))
            result["properties"] = filtered_properties
        return result

    def _process_compositions(self, result: dict, tool_name: str, server_name: str, path: list, extractions: list) -> None:
        for key in ("allOf", "anyOf", "oneOf"):
            if key in result:
                result[key] = [self._process_node(item, tool_name, server_name, [*path, {"type": key, "index": i}], extractions) for i, item in enumerate(result[key])]
        for key in ("if", "then", "else"):
            if key in result:
                result[key] = self._process_node(result[key], tool_name, server_name, [*path, {"type": key}], extractions)
        if "not" in result:
            result["not"] = self._process_node(result["not"], tool_name, server_name, [*path, {"type": "not"}], extractions)
        if "items" in result:
            if isinstance(result["items"], dict):
                result["items"] = self._process_node(result["items"], tool_name, server_name, [*path, {"type": "items"}], extractions)
            elif isinstance(result["items"], list):
                result["items"] = [self._process_node(item, tool_name, server_name, [*path, {"type": "items", "index": i}], extractions) for i, item in enumerate(result["items"])]
        for key in ("contains", "propertyNames", "additionalProperties"):
            if key in result and isinstance(result[key], dict):
                result[key] = self._process_node(result[key], tool_name, server_name, [*path, {"type": key}], extractions)
        if "patternProperties" in result and isinstance(result["patternProperties"], dict):
            for pat, sub in list(result["patternProperties"].items()):
                result["patternProperties"][pat] = self._process_node(sub, tool_name, server_name, [*path, {"type": "patternProperties", "pattern": pat}], extractions)

    def _build_property_file(self, tool_name: str, path: list, leaf_schema: dict) -> dict:
        current = leaf_schema
        for segment in reversed(path):
            seg_type = segment["type"]
            if seg_type == "properties": current = {"properties": {segment["name"]: current}}
            elif seg_type == "items":
                if "index" in segment: current = {"items": [current]}
                else: current = {"items": current}
            elif seg_type in ("allOf", "anyOf", "oneOf"): current = {seg_type: [current]}
            elif seg_type == "additionalProperties": current = {"additionalProperties": current}
            elif seg_type == "patternProperties": current = {"patternProperties": {segment["pattern"]: current}}
            elif seg_type in ("if", "then", "else", "not", "contains", "propertyNames"): current = {seg_type: current}
        return {"name": tool_name, "inputSchema": current}

    def _prepare_tool(self, server_name: str, tool: Any) -> None:
        tool_name = tool.name
        # Determine unique frontend name
        # If server_name happens twice at the start, don't double it.
        # But specifically as per instructions: "Add into the tool name the {server_name}_ prefix only if it already does not have it."
        # If server_name is "github" and tool is "github__list_prs", check for "github_" prefix.
        prefix = f"{server_name}_"
        frontend_name = tool_name if tool_name.startswith(prefix) else f"{server_name}_{tool_name}"

        self.tool_mapping[frontend_name] = (server_name, tool_name)

        tid = frontend_name # using frontend_name as the ID in tools.json too
        input_schema = copy.deepcopy(tool.inputSchema)
        full_schema = {
            "name": tool_name,
            "description": tool.description,
            "inputSchema": input_schema,
        }
        self._collect_enums(input_schema)

        full_file = SCHEMAS_DIR / "full" / server_name / f"{tool_name}.json"
        self._smart_write(full_file, json.dumps(full_schema, indent=2))

        self.discovered_tools.append({
            "id": tid,
            "server": server_name,
            "tool": tool_name,
            "summary": self._truncate_description(tool.description or ""),
            "full_schema": full_schema,
        })

    def _write_catalog_files(self) -> None:
        # Enums
        seen = set()
        unique_enums = []
        for val in self.all_enums:
            key = json.dumps(val, sort_keys=True)
            if key not in seen:
                seen.add(key); unique_enums.append(val)
        unique_enums.sort(key=lambda x: json.dumps(x, sort_keys=True))
        for val in unique_enums:
            self._smart_write(SCHEMAS_DIR / "decomposed" / f"{val}.md", str(val))

        # Tools and properties
        for tool_info in self.discovered_tools:
            server_name = tool_info["server"]
            tool_name = tool_info["tool"]
            tool_id = self._get_tool_id(server_name, tool_name)
            description = tool_info["full_schema"]["description"]
            input_schema = copy.deepcopy(tool_info["full_schema"]["inputSchema"])
            extractions = []
            filtered = self._process_node(input_schema, tool_name, server_name, [], extractions) if isinstance(input_schema, dict) else input_schema

            self._smart_write(SCHEMAS_DIR / "decomposed" / server_name / f"{tool_id}.json", json.dumps({"name": tool_name, "description": description, "inputSchema": filtered}, indent=2))

            for path_segments, prop_schema in extractions:
                prop_name = path_segments[-1]["name"]
                prop_dir = SCHEMAS_DIR / "decomposed" / server_name / tool_id
                for seg in path_segments[:-1]:
                    if seg["type"] == "properties": prop_dir = prop_dir / seg["name"]
                    elif seg["type"] == "patternProperties": prop_dir = prop_dir / seg["pattern"]
                self._smart_write(prop_dir / f"{prop_name}.json", json.dumps(prop_schema, indent=2))

        self._smart_write(OUT / "tools.json", json.dumps(self.discovered_tools, indent=2))
        self._apply_outputs()
        self._prune_stale_files(OUT, set(self.output_map.keys()))

    async def initialize(self) -> None:
        config_path = Path.home() / ".claude.json"
        if not config_path.exists():
            logger.error(f"~/.claude.json not found")
            return
        try:
            config = json.loads(config_path.read_text()).get("mcpServers", {})
        except Exception as e:
            logger.error(f"Error reading config: {e}")
            return

        OUT.mkdir(exist_ok=True, parents=True)
        SCHEMAS_DIR.mkdir(exist_ok=True, parents=True)

        current_script = Path(__file__).resolve()
        for server_name, server_config in config.items():
            # Prevent self-recursion by name or path
            if server_name == self.mcp.name:
                logger.warning(f"Skipping server '{server_name}' - matches aggregator name")
                continue

            command = server_config.get("command")
            args = server_config.get("args", [])
            is_self = False
            for part in [command] + (args if isinstance(args, list) else []):
                if not part or not isinstance(part, str):
                    continue
                try:
                    p = Path(part)
                    if p.is_file() and p.resolve() == current_script:
                        is_self = True
                        break
                    # Also check if it's being run via a parent directory (e.g. module)
                    if p.is_dir() and p.resolve() == current_script.parent:
                        if "aggregator" in part:
                            is_self = True
                            break
                except Exception:
                    continue

            if is_self:
                logger.warning(f"Skipping server '{server_name}' to prevent self-recursion (path match)")
                continue

            logger.info(f"Connecting to {server_name}...")
            client = Client({"mcpServers": {server_name: server_config}})
            try:
                # We want to keep these connections open
                await client.__aenter__()

                # Detect by reported server name before loading tools
                remote_info = client.initialize_result
                if remote_info and remote_info.serverInfo.name == self.mcp.name:
                    logger.warning(f"Skipping server '{server_name}' - reported name matches ours: '{self.mcp.name}'")
                    await client.__aexit__(None, None, None)
                    continue

                self.clients[server_name] = client
                tools = await client.list_tools()
                logger.info(f"  Discovered {len(tools)} tools on {server_name}")
                for tool in tools:
                    self._prepare_tool(server_name, tool)
            except Exception as e:
                logger.error(f"Failed to connect to {server_name}: {e}")
                # Don't fail the whole proxy if one server is down

        self._write_catalog_files()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all discovered tools with the FastMCP frontend."""
        for frontend_name, (server_name, backend_name) in self.tool_mapping.items():
            # Find the original tool to get description and schema
            tool_info = next((t for t in self.discovered_tools if t["server"] == server_name and t["tool"] == backend_name), None)
            if not tool_info:
                continue

            description = tool_info["full_schema"].get("description", "")
            input_schema = tool_info["full_schema"].get("inputSchema", {})

            # Create a tool-specific handler with a concrete signature to satisfy FastMCP inspection.
            def make_handler(sn, bn, schema):
                # Extract argument names from schema
                properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
                arg_names = [name for name in properties.keys() if name.isidentifier()]

                # Create a function body that calls the backend client
                # Using unique arg names ensures no collisions with our internal variables
                args_str = ", ".join(arg_names)

                # Build the dynamic function code
                # We use a wrapper to capture the current sn and bn
                code = f"async def handler(self, {args_str}):\n"
                code += f"    params = {{{', '.join([f'\"{name}\": {name}' for name in arg_names])}}}\n"
                code += f"    client = self.clients.get(\"{sn}\")\n"
                code += f"    if not client:\n"
                code += f"        return \"Error: Backend server {sn} is not connected\"\n"
                code += f"    try:\n"
                code += f"        return await client.call_tool(\"{bn}\", params)\n"
                code += f"    except Exception as e:\n"
                code += f"        return f\"Error calling {bn} on {sn}: {{str(e)}}\"\n"

                loc = {}
                # We need self in the scope for the handler to access clients
                exec(code, globals(), loc)
                return loc["handler"]

            try:
                # Register the tool using the FastMCP decorator pattern manually
                handler = make_handler(server_name, backend_name, input_schema)
                # Bind the handler to self so it has access to self.clients
                import types
                bound_handler = types.MethodType(handler, self)

                # FastMCP uses the function __name__ for the tool name if not overridden.
                # It also inspects the signature for argument types.
                # Since we generated it with exec, it should have the correct signature.
                self.mcp.tool(name=frontend_name, description=description)(bound_handler)
            except Exception as e:
                logger.error(f"Failed to register tool {frontend_name}: {e}")

    async def run(self, transport: str = "http") -> None:
        try:
            await self.initialize()
            logger.info(f"Starting MCP Semantic Capability Attention (SCA) on {transport} transport...")
            await self.mcp.run_async(transport=transport)
        finally:
            # Cleanup connections
            for client in self.clients.values():
                try:
                    await client.__aexit__(None, None, None)
                except:
                    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Aggregator")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http", help="MCP transport protocol (default: http)")
    args = parser.parse_args()

    aggregator = MCPAggregator()
    asyncio.run(aggregator.run(transport=str(args.transport)))