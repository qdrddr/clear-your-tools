import asyncio
import json
from pathlib import Path

import anyio

from fastmcp import Client


async def check() -> None:
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        print("Config not found")
        return

    config = json.loads(await anyio.Path(config_path).read_text())

    mcp_servers = config.get("mcpServers", {})
    client = Client({"mcpServers": mcp_servers})

    async with client:
        tools = await client.list_tools()
        if tools:
            tool = tools[0]
            print(f"Tool: {tool.name}")
            print(f"Attributes: {dir(tool)}")
            # Try to see if it has server_name or similar
            for attr in ["server", "server_name", "mcp_server", "origin", "meta", "title"]:
                if hasattr(tool, attr):
                    print(f"{attr}: {getattr(tool, attr)}")
            if hasattr(tool, "meta") and tool.meta:
                print(f"meta keys: {tool.meta.keys() if hasattr(tool.meta, 'keys') else 'no keys'}")
                print(f"meta content: {tool.meta}")


if __name__ == "__main__":
    asyncio.run(check())
