import asyncio
import json
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any, Literal, final

import httpx
import uvicorn
from fastmcp import Client, FastMCP, Context
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.server.transforms.visibility import save_visibility_rules

from recursion import (
    check_self_recursion_protection,
    is_mcp_aggregator_description,
    is_self_recursion,
)
from build_index import CatalogBuilder
from retrieve_catalog import load_catalog, parse_json_input, _group_files, _process_groups
from rerank import rerank_items, extract_document_text
from llm import prepare_chunks, call_llm, process_results
from relay import RelayServer, RELAY_IDENTITY

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000

@final
class MCPAggregator:
    def __init__(self, debug: bool = False) -> None:
        self.mcp = FastMCP("MCP Aggregator")
        self.debug = debug
        self.clients: dict[str, Client[Any]] = {}
        self.tool_mapping: dict[str, tuple[str, str]] = {}
        self.catalog = CatalogBuilder()
        self.session_tools: dict[str, list[str]] = {} # session_id -> list of tool names
        self.current_session_id: str | None = None
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
                # In STDIO mode, the session ID is passed via environment or we relay on the one we registered
                session_id = aggregator_self.current_session_id

                # Check if we have filtered tools for this session
                if session_id and session_id in aggregator_self.session_tools:
                    allowed = aggregator_self.session_tools[session_id]
                    logger.debug("Applying session filtering for %s: %d tools", session_id, len(allowed))
                    rules = [
                        {"enabled": False, "match_all": True},
                        {"enabled": True, "names": allowed, "components": ["tool"]},
                    ]
                    await save_visibility_rules(context.fastmcp_context, rules)
                elif not aggregator_self.debug:
                    # Default: empty tool list at startup until query received
                    rules = [{"enabled": False, "match_all": True}]
                    await save_visibility_rules(context.fastmcp_context, rules)
                # If debug is True and no session tools yet, we don't apply any rules,
                # meaning all tools are visible by default.

                return await call_next(context)

        self.mcp.add_middleware(SessionMiddleware())

    def _setup_orchestrator_tools(self) -> None:
        @self.mcp.tool()
        async def list_aggregated_tools() -> list[dict[str, Any]]:
            """List all tools currently aggregated from backend servers."""
            return self.catalog.discovered_tools

    async def run_filtering_pipeline(self, prompt: str) -> list[dict[str, Any]]:
        # 1. Select the relevant tools via cs.py
        import src.cs as cs
        from src.retrieve_catalog import parse_json_input, _group_files, _process_groups

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            try:
                # Based on src/cs.py, the search function takes query as a list of strings
                cs.search(query=[prompt], json_output=True)
            except SystemExit:
                # Typer commands might call sys.exit(0) on success
                pass
            except Exception as e:
                logger.error("Error running cs.search: %s", e)
                return []

        # Find the JSON part in the output (in case there's other text)
        output = f.getvalue()
        try:
            # Look for the last valid JSON block in output
            # cs.py search writes JSON followed by \n
            start_idx = output.find('{')
            if start_idx == -1:
                logger.error("No JSON found in cs.py output: %s", output)
                return []
            json_str = output[start_idx:]
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("Failed to parse cs.py output as JSON: %s", output)
            return []

        # 2. Reconstruct tool schemas via retrieve_catalog
        input_files, scores = parse_json_input(data)
        decomposed_dir = Path("src/catalog/schemas/decomposed")

        # Ensure decomposed_dir exists relative to current working directory
        # If gwr.py is run from root, src/catalog/schemas/decomposed should be correct
        if not decomposed_dir.exists():
             # Try absolute path based on file location
             decomposed_dir = Path(__file__).parent / "catalog" / "schemas" / "decomposed"

        groups, tool_files = _group_files(input_files, decomposed_dir)
        final_tools = _process_groups(groups, tool_files, scores, decomposed_dir)

        return final_tools

    async def _handle_events(self, relay_url: str) -> None:
        ppid = self.current_session_id
        if not ppid:
            logger.warning("PPID not set; Gateway Runtime will not receive events.")
            return

        # 1. Query Relay for pending events on startup
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{relay_url}/list_sessions")
                if resp.status_code == 200:
                    sessions = resp.json()
                    for session in sessions:
                        if session.get("ppid") == ppid:
                            # Requirement 3: If session already has "tools" metadata, use it
                            existing_tools = session.get("tools")
                            if existing_tools:
                                logger.info("Found existing tools in session metadata for PPID %s", ppid)
                                await self._refresh_tools(prompt="", relay_url=relay_url, tools=existing_tools)

                            pending_events = session.get("events", [])
                            for event in pending_events:
                                if not event.get("ack") and event.get("type") == "search":
                                    prompt = event.get("prompt")
                                    # Check if the event itself has pre-selected tools
                                    tools = event.get("tools")
                                    if prompt or tools:
                                        logger.info("Processing pending event: %s (has_tools=%s)", prompt, tools is not None)
                                        await self._refresh_tools(prompt or "", relay_url, tools=tools)
            except Exception as e:
                logger.error("Error fetching pending events: %s", e)

        # 2. Subscribe to SSE
        logger.info("Subscribing to Relay events for ppid: %s", ppid)
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", f"{relay_url}/events/{ppid}") as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            event_data = json.loads(line[6:])
                            if event_data.get("type") == "search":
                                prompt = event_data.get("prompt") or event_data.get("query")
                                tools = event_data.get("tools")
                                logger.info("Received search event: %s (has_tools=%s)", prompt, tools is not None)
                                if prompt or tools:
                                    await self._refresh_tools(prompt or "", relay_url, tools=tools)
            except Exception as e:
                logger.error("Error in event stream: %s", e)

    async def _refresh_tools(self, prompt: str, relay_url: str | None = None, tools: list[dict[str, Any]] | None = None) -> None:
        if tools is not None and len(tools) > 0:
            logger.info("Using provided tool list for PPID %s (skipping pipeline)", self.current_session_id)
            filtered_tools = tools
        else:
            if not prompt:
                logger.warning("No prompt and no tools provided for refresh, skipping.")
                return
            filtered_tools = await self.run_filtering_pipeline(prompt)

        new_tool_names = [t["name"] for t in filtered_tools]

        # Compare with existing tools for session
        old_tool_names = self.session_tools.get(self.current_session_id, [])

        # Trigger if anything is different (simple comparison for now)
        if set(new_tool_names) != set(old_tool_names):
            logger.info("Tools list changed for PPID %s. Emitting tools/list_changed with %d tools.", self.current_session_id, len(filtered_tools))
            self.session_tools[self.current_session_id] = new_tool_names
            try:
                # Emit tools/list_changed via STDIO to the connected MCP Client
                await self.mcp._mcp_server.send_notification(
                    "notifications/tools/list_changed",
                    {"tools": filtered_tools}
                )
            except Exception as e:
                logger.debug("Failed to send tools/list_changed: %s", e)

        # 3. Acknowledge the event and store the tool list in the Relay
        if relay_url:
            async with httpx.AsyncClient() as client:
                try:
                    await client.post(
                        f"{relay_url}/ack/ppid/{self.current_session_id}",
                        json={"tools": filtered_tools}
                    )
                except Exception as e:
                    logger.error("Failed to acknowledge event: %s", e)

    async def initialize(self, config_paths: list[Path]) -> None:
        if check_self_recursion_protection():
            logger.warning("Another aggregator instance detected, proceeding in attachment mode...")
        config = self._get_mcp_config(config_paths)
        if not config:
            return

        current_script = Path(__file__).resolve()
        filtered_config = await self._discover_and_filter_servers(config, current_script)

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
                servers = data.get("mcpServers", {})
                combined_servers.update(servers)
            except Exception:
                logger.exception("Error reading config from %s", config_path)
        return combined_servers

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
            for tool in tools:
                self._prepare_tool(s_name, tool)
        except Exception as e:
            logger.error("Failed to connect to %s: %s", s_name, e)
            await client.__aexit__(None, None, None)

    async def _discover_and_filter_servers(self, config, current_script):
        valid_configs = {}
        for s_name, s_config in config.items():
            if is_self_recursion(s_name, s_config, current_script, self.mcp.name):
                continue
            client = Client({"mcpServers": {s_name: s_config}})
            try:
                await client.__aenter__()
                remote_info = client.initialize_result
                if remote_info:
                    remote_name = remote_info.serverInfo.name
                    if not is_mcp_aggregator_description(remote_name, self.mcp.name):
                        valid_configs[s_name] = s_config
            except Exception:
                pass
            finally:
                await client.__aexit__(None, None, None)
        return valid_configs

    def _register_tools(self) -> None:
        for f_name, (sn, bn) in self.tool_mapping.items():
            tool_info = self.catalog.get_tool_info(sn, bn)
            if not tool_info: continue
            desc = tool_info["full_schema"].get("description", "")
            schema = tool_info["full_schema"].get("inputSchema", {})

            def make_handler(sn_val: str, bn_val: str, schema_val: dict[str, Any]) -> Any:
                props = schema_val.get("properties", {}) if isinstance(schema_val, dict) else {}
                arg_names = [name for name in props.keys() if name.isidentifier()]
                args_str = ", ".join(["self", "ctx: Context"] + arg_names)
                code_lines = [
                    f"async def handler({args_str}):",
                    f'    client = self.clients.get("{sn_val}")',
                    "    params = {" + ", ".join([f"'{name}': {name}" for name in arg_names]) + "}",
                    f'    return await client.call_tool("{bn_val}", params)'
                ]
                loc = {"logger": logger, "Context": Context}
                # Use globals from gwr.py context
                exec("\n".join(code_lines), globals(), loc)
                return loc["handler"]

            handler = make_handler(sn, bn, schema)
            self.mcp.tool(name=f_name, description=desc)(types.MethodType(handler, self))

    async def run(
        self,
        config_paths: list[Path],
        transport: Literal["stdio", "http"] = "http",
        port: int = DEFAULT_PORT,
        only_relay: bool = False,
    ) -> None:
        if not only_relay:
            await self.initialize(config_paths)
            self.current_session_id = os.environ.get("PPID")
            if self.current_session_id:
                logger.info("MCPAggregator initialized with session_id: %s", self.current_session_id)
            else:
                logger.warning("MCPAggregator initialized WITHOUT PPID")

        host = "localhost"

        # Relay setup
        relay_url = f"http://{host}:{port}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{relay_url}/health", timeout=1.0)
                if resp.status_code == 200 and resp.json().get("name") == RELAY_IDENTITY:
                    logger.info("Relay Server already running on port %d, attaching.", port)
                else:
                    logger.error("Port %d is occupied by non-aggregator service; cannot continue.", port)
                    sys.exit(1)
        except (httpx.ConnectError, httpx.TimeoutException):
            logger.info("Starting local Relay Server on port %d", port)
            relay = RelayServer(debug=self.debug, only_relay=only_relay)
            config = uvicorn.Config(relay.app, host=host, port=port, log_level="error")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            # Wait for relay to start
            await asyncio.sleep(1)

        # Register session if we have one and not in only-relay mode
        if not only_relay:
            logger.info("Aggregator: Checking if registration is needed. current_session_id=%s", self.current_session_id)
            if self.current_session_id:
                logger.info("Aggregator: Attempting to register session %s with relay at %s", self.current_session_id, relay_url)
                async with httpx.AsyncClient() as client:
                    try:
                        async with client.post(
                            f"{relay_url}/set/ppid/{self.current_session_id}",
                            json={"ppid": self.current_session_id}
                        ) as resp:
                            # Read response if needed or just log
                            data = await resp.json()
                            logger.info("Aggregator: Session registration response: %s %s", resp.status_code, data)

                            # Requirement: If prompt exists in the existing session and tools are empty, trigger a refresh
                            # list_sessions returns a list of sessions
                            resp_list = await client.get(f"{relay_url}/list_sessions")
                            if resp_list.status_code == 200:
                                sessions = resp_list.json()
                                for session in sessions:
                                    if session.get("ppid") == self.current_session_id:
                                        prompt = session.get("prompt")
                                        tools = session.get("tools")
                                        if prompt and (not tools or len(tools) == 0):
                                            logger.info("Initial registration found prompt but no tools. Triggering extraction.")
                                            asyncio.create_task(self._refresh_tools(prompt, relay_url))
                                        break
                    except Exception as e:
                        logger.error("Aggregator: Failed to register and check initial state: %s", e)
            else:
                logger.warning("Aggregator: No current_session_id found in environment, skipping registration.")

            asyncio.create_task(self._handle_events(relay_url))

        if only_relay:
            logger.info("MCP Server will not be started.")
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            return

        # Always use stdio for the MCP server when running alongside or attached to a relay
        # This prevents starting a second HTTP server on port+1 (8082)
        logger.info("Forcing transport to stdio to integrate with Relay Server.")
        transport = "stdio"

        if transport == "stdio":
            # Redirect stdout logging to stderr
            for h in logging.root.handlers:
                if isinstance(h, logging.StreamHandler) and h.stream == sys.stdout:
                    h.setStream(sys.stderr)
            await self.mcp.run_async(transport="stdio")
        else:
            await self.mcp.run_async(transport="http", port=port+1) # Run MCP on different port if http
