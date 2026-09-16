"""Per-git-project MCP tool call example memory.

One shared examples database (``~/.config/cyt/tool_examples.db``) stores successful
calls keyed by project root plus backend ``(mcp_server, tool_name)``. User-origin
and workspace-origin MCP tools share the same project pool — injection scope
(``cyt-mcp-usr`` vs ``cyt-mcp-ws``) affects catalog layout only, not example storage.
"""

from typing import TYPE_CHECKING

from cyt.tool_examples.config import examples_active, tool_examples_db_path
from cyt.tool_examples.store import ToolExamplesStore

if TYPE_CHECKING:
    from cyt.tool_examples.enrich import enrich_tools_with_examples

__all__ = [
    "ToolExamplesStore",
    "enrich_tools_with_examples",
    "examples_active",
    "tool_examples_db_path",
]


def __getattr__(name: str) -> object:
    if name == "enrich_tools_with_examples":
        from cyt.tool_examples.enrich import enrich_tools_with_examples

        return enrich_tools_with_examples
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
