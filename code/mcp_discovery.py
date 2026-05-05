import asyncio
import json
from pathlib import Path
from fastmcp import Client

async def discover_server_primitives() -> None:
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        print(f"Error: {config_path} not found.")
        return

    with open(config_path) as f:
        full_config = json.load(f)

    mcp_servers_config = full_config.get("mcpServers", {})
    if not mcp_servers_config:
        print("No mcpServers found in ~/.claude.json")
        return

    # Filter out disabled servers if any (from the current project section)
    # The user asked specifically for root mcpServers.
    # Some servers might be disabled globally or in projects, but sticking to requested root list.

    config = {"mcpServers": mcp_servers_config}
    client = Client(config)

    print(f"Connecting to {len(mcp_servers_config)} servers...")

    async with client:
        # Discover tools
        tools = await client.list_tools()
        print(f"\n[Tools] ({len(tools)})")
        for tool in tools:
            print(f" - {tool.name}: {tool.description}")

        # Discover prompts
        prompts = await client.list_prompts()
        print(f"\n[Prompts] ({len(prompts)})")
        for prompt in prompts:
            print(f" - {prompt.name}: {prompt.description}")

        # Discover resources
        resources = await client.list_resources()
        print(f"\n[Resources] ({len(resources)})")
        for resource in resources:
            print(f" - {resource.uri}: {resource.name}")

if __name__ == "__main__":
    asyncio.run(discover_server_primitives())
