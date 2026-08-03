from collections.abc import Sequence

from fastmcp.tools.tool import Tool

class Transform:
    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]: ...
