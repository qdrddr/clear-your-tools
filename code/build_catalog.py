import asyncio
import json
import logging
from pathlib import Path

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


async def build() -> None:
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        print(f"Error: {config_path} not found.")
        return

    try:
        full_config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error reading {config_path}: {e}")
        return

    mcp_servers_config = full_config.get("mcpServers", {})
    if not mcp_servers_config:
        print("No mcpServers found in ~/.claude.json")
        return

    # Filter out servers that are explicitly disabled in the root config if any
    # But usually mcpServers contains all configured ones.
    # Some might be disabled project-wise, but we want the global catalog.

    print(f"Connecting to {len(mcp_servers_config)} servers...")

    # Ensure directories exist
    OUT.mkdir(exist_ok=True)

    # Catalog wiping: clear existing schemas
    if SCHEMAS_DIR.exists():
        for f in SCHEMAS_DIR.glob("*.json"):
            f.unlink()
    else:
        SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)

    client = Client({"mcpServers": mcp_servers_config})

    discovered_tools = []

    try:
        async with client:
            tools = await client.list_tools()
            print(f"Discovered {len(tools)} tools.")

            for tool in tools:
                # FastMCP prefixes multi-server tool names as {server_name}_{tool_name}
                tid = tool.name

                # Real MCP tool descriptions can be verbose
                summary = _truncate_description(tool.description or "")

                # Full schema expected by consumer
                full_schema = {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                }

                discovered_tools.append({"id": tid, "summary": summary, "full_schema": full_schema})

                # Write individual schema file
                schema_file = SCHEMAS_DIR / f"{tid}.json"
                schema_file.write_text(json.dumps(full_schema, indent=2))

    except Exception as e:
        print(f"Warning: Discovered tools might be incomplete due to error: {e}")

    if not discovered_tools:
        print("No tools discovered.")
        return

    # Write tools.json
    (OUT / "tools.json").write_text(json.dumps(discovered_tools, indent=2))

    print(f"Successfully wrote {len(discovered_tools)} tools to {OUT / 'tools.json'}")
    print(f"Individual schemas written to {SCHEMAS_DIR}")


if __name__ == "__main__":
    asyncio.run(build())
