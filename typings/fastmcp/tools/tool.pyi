from collections.abc import Callable
from typing import Any

from fastmcp.tools.base import ToolResult

class Tool:
    name: str
    parameters: dict[str, Any]
    description: str | None
    output_schema: dict[str, Any] | None
    def __init__(
        self,
        *,
        name: str = ...,
        parameters: dict[str, Any] = ...,
        description: str | None = ...,
        output_schema: dict[str, Any] | None = ...,
        **kwargs: Any,
    ) -> None: ...
    @classmethod
    def from_function(cls, fn: Callable[..., Any], **kwargs: Any) -> Tool: ...
    @classmethod
    def from_tool(cls, tool: Tool, **kwargs: Any) -> Tool: ...
    def model_copy(self, *, update: dict[str, Any] | None = ..., **kwargs: Any) -> Tool: ...
    def to_mcp_tool(self) -> Any: ...
    async def run(self, arguments: dict[str, Any]) -> ToolResult: ...
    def convert_result(self, result: Any) -> ToolResult: ...
