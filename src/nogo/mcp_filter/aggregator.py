import argparse
import asyncio
import logging
import os
from pathlib import Path

from gwr import MCPAggregator
from configs import DEFAULT_MCP_AGGREGATOR_PORT

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Aggregator")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGGREGATOR_PORT", DEFAULT_MCP_AGGREGATOR_PORT)))
    parser.add_argument("--servers", nargs="+", help="Path(s) to MCP JSON config files.")
    parser.add_argument("--only-relay", action="store_true", help="Start only the Relay Server.")
    parser.add_argument("--debug", action="store_true", help="Start with full tool list.")
    cmd_args = parser.parse_args()

    default_paths = [Path.home() / ".claude.json", Path(".mcp.json").resolve()]
    config_paths = [Path(p).expanduser().resolve() for p in (cmd_args.servers or [])] or [p for p in default_paths if p.exists()]

    aggregator = MCPAggregator(debug=cmd_args.debug)
    asyncio.run(aggregator.run(
        config_paths=config_paths,
        transport=cmd_args.transport,
        port=cmd_args.port,
        only_relay=cmd_args.only_relay
    ))
