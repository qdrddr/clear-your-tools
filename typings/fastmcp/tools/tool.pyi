from collections.abc import Callable
from typing import Any

class Tool:
    name: str
    description: str | None
    title: str | None
    inputSchema: dict[str, Any]
    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str,
    ) -> Tool: ...
    @classmethod
    def from_tool(cls, tool: Tool, *, description: str) -> Tool: ...
    def to_mcp_tool(self) -> Tool: ...
    def model_copy(self, *, update: dict[str, Any] | None = None) -> Tool: ...
