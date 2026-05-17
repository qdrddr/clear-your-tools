import argparse
import asyncio
import copy
import json
import logging
import types
from recursion import (
    check_self_recursion_protection,
    is_mcp_aggregator_description,
    is_self_recursion,
)
from build_index import CatalogBuilder
from pathlib import Path
from typing import Any, Literal, cast, final

from fastmcp import Client, FastMCP, Context
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.transforms.visibility import save_visibility_rules
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@final
class MCPAggregator:
    def __init__(self) -> None:
        self.mcp = FastMCP("MCP Aggregator")
        self.clients: dict[str, Client[Any]] = {}
        self.tool_mapping: dict[
            str,
            tuple[str, str],
        ] = {}  # frontend_name -> (server_name, backend_name)
        self.catalog = CatalogBuilder()
        self.session_allowed_tools: dict[str, list[str]] = {}  # session_id -> [tool_names]
        self._setup_middleware()
        self._setup_orchestrator_tools()

    def _setup_middleware(self) -> None:
        aggregator_self = self

        class SessionMiddleware(Middleware):
            async def __call__(
                self,
                context: MiddlewareContext[Any],
                call_next: CallNext[Any, Any],
            ) -> Any:
                request = get_http_request()
                claude_session_id = None
                if request:
                    claude_session_id = request.headers.get("X-Claude-Session-Id")

                # Log on initialization
                if context.method == "tools/list":
                    logger.info(
                        "New MCP client session initialization. X-Claude-Session-Id: %s",
                        claude_session_id or "MISSING",
                    )
                elif claude_session_id:
                    logger.debug(
                        "Captured X-Claude-Session-Id: %s for method: %s",
                        claude_session_id,
                        context.method,
                    )

                # Store session_id in context state
                if context.fastmcp_context:
                    await context.fastmcp_context.set_state(
                        "claude_session_id", claude_session_id, serializable=False
                    )

                    # Update visibility rules
                    if claude_session_id:
                        allowed = aggregator_self.session_allowed_tools.get(claude_session_id)
                        if allowed is not None:
                            rules = [
                                {"enabled": False, "match_all": True},
                                {"enabled": True, "names": allowed, "components": ["tool"]},
                            ]
                        else:
                            rules = [{"enabled": False, "match_all": True}]
                        await save_visibility_rules(context.fastmcp_context, rules)
                    else:
                        await save_visibility_rules(context.fastmcp_context, [])

                return await call_next(context)

        self.mcp.add_middleware(SessionMiddleware())

    def _setup_orchestrator_tools(self) -> None:
        @self.mcp.tool()
        async def list_aggregated_tools() -> list[dict[str, Any]]:
            """List all tools currently aggregated from backend servers."""
            return self.catalog.discovered_tools

        @self.mcp.tool()
        async def set_allowed_tools(claude_session_id: str, tool_names: list[str]) -> str:
            """Set the list of tools allowed for a specific Claude session."""
            self.session_allowed_tools[claude_session_id] = tool_names
            logger.info(
                "Updated allowed tools for session %s: %s", claude_session_id, tool_names
            )
            return f"Updated allowed tools for session {claude_session_id}"

    def _prepare_tool(self, server_name: str, tool: Any) -> None:
        frontend_name = self.catalog.prepare_tool(server_name, tool)
        self.tool_mapping[frontend_name] = (server_name, tool.name)

    async def _connect_to_server(self, s_name: str, s_config: dict[str, Any]) -> None:
        logger.info("Connecting to %s...", s_name)
        client: Client[Any] = Client({"mcpServers": {s_name: s_config}})
        try:
            await client.__aenter__()
            self.clients[s_name] = client
            tools = await client.list_tools()
            logger.info("  Discovered %d tools on %s", len(tools), s_name)
            for tool in tools:
                self._prepare_tool(s_name, tool)
        except Exception as e:
            logger.error("Failed to connect to %s: %s", s_name, e)
            try:
                await client.__aexit__(None, None, None)
            except (Exception, BaseException):
                pass

    async def _discover_and_filter_servers(
        self,
        config: dict[str, dict[str, Any]],
        current_script: Path,
    ) -> dict[str, dict[str, Any]]:
        """Identify servers that are not aggregators by temporarily connecting to them."""
        valid_configs: dict[str, dict[str, Any]] = {}
        for s_name, s_config in config.items():
            if is_self_recursion(s_name, s_config, current_script, self.mcp.name):
                continue

            logger.info("Discovering server info for %s...", s_name)
            client: Client[Any] = Client({"mcpServers": {s_name: s_config}})
            try:
                await client.__aenter__()
                remote_info = client.initialize_result
                if remote_info:
                    remote_name = remote_info.serverInfo.name
                    logger.info("Server '%s' reports as '%s'", s_name, remote_name)
                    if is_mcp_aggregator_description(remote_name, self.mcp.name):
                        logger.warning(
                            "Excluding server '%s' - matches aggregator description",
                            s_name,
                        )
                    else:
                        valid_configs[s_name] = s_config
            except Exception as e:
                logger.error("Failed discovery for %s: %s", s_name, e)
            finally:
                try:
                    await asyncio.sleep(0.1)  # Brief grace period for stdio tasks
                    await client.__aexit__(None, None, None)
                except BaseException:
                    # During discovery, ignore all errors on exit to prevent ExceptionGroup crashes
                    pass
        return valid_configs

    async def initialize(self, config_paths: list[Path]) -> None:
        check_self_recursion_protection()
        config = self._get_mcp_config(config_paths)
        if not config:
            return

        current_script = Path(__file__).resolve()

        # Step 1-3: Discover, stop all, and check for self-recursion
        filtered_config = await self._discover_and_filter_servers(config, current_script)

        # Step 4: Re-connect to valid survivors
        for s_name, s_config in filtered_config.items():
            await self._connect_to_server(s_name, s_config)

        self.catalog.write_catalog()
        self._register_tools()

    def _get_mcp_config(self, config_paths: list[Path]) -> dict[str, dict[str, Any]]:
        combined_servers: dict[str, dict[str, Any]] = {}
        for config_path in config_paths:
            if not config_path.exists():
                logger.warning("Config file %s not found", config_path)
                continue
            try:
                data = json.loads(config_path.read_text())
                if not isinstance(data, dict):
                    logger.warning("Config file %s is not a JSON object", config_path)
                    continue
                servers = data.get("mcpServers", {})
                if not isinstance(servers, dict):
                    logger.warning("mcpServers in %s is not an object", config_path)
                    continue
                combined_servers.update(servers)
            except Exception:
                logger.exception("Error reading config from %s", config_path)
        return combined_servers

    def _register_tools(self) -> None:
        for f_name, (sn, bn) in self.tool_mapping.items():
            tool_info = self.catalog.get_tool_info(sn, bn)
            if not tool_info:
                continue

            desc: str = tool_info["full_schema"].get("description", "")
            schema: dict[str, Any] = tool_info["full_schema"].get("inputSchema", {})

            def make_handler(sn_val: str, bn_val: str, schema_val: dict[str, Any]) -> Any:
                props: dict[str, Any] = schema_val if isinstance(schema_val, dict) else {}
                props = props.get("properties", {})
                arg_names = [name for name in props.keys() if name.isidentifier()]
                # Prepend ctx: Context to the argument list
                args_list = ["self", "ctx: Context"] + arg_names
                args_str = ", ".join(args_list)

                code_lines = [
                    f"async def handler({args_str}):",
                    "    session_id = await ctx.get_state('claude_session_id')",
                    "    logger.info('Handling tool call with session_id: %s', session_id)",
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
                loc: dict[str, Any] = {"logger": logger, "Context": Context}
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
        config_paths: list[Path],
        transport: Literal["stdio", "http", "sse", "streamable-http"] = "http",
        port: int | None = None,
    ) -> None:
        try:
            await self.initialize(config_paths)
            logger.info("Starting SCA on %s transport...", transport)
            await self.mcp.run_async(transport=transport, port=port)
        finally:
            for client in self.clients.values():
                try:
                    await client.__aexit__(None, None, None)
                except BaseException:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Aggregator")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--port", type=int, help="HTTP port to listen on (overrides default 8000)")
    parser.add_argument(
        "--servers",
        nargs="+",
        help="Path(s) to MCP JSON config files. Merges mcpServers keys.",
    )
    cmd_args = parser.parse_args()

    if cmd_args.servers:
        config_paths = [Path(p).expanduser().resolve() for p in cmd_args.servers]
    else:
        default_paths = [
            Path.home() / ".claude.json",
            Path(".mcp.json").resolve(),
        ]
        config_paths = [p for p in default_paths if p.exists()]
    aggregator = MCPAggregator()
    transport = cast(Literal["stdio", "http", "sse", "streamable-http"], cmd_args.transport)
    port = cast(int | None, cmd_args.port)
    if port:
        transport = "http"

    asyncio.run(
        aggregator.run(
            config_paths=config_paths,
            transport=transport,
            port=port,
        ),
    )
