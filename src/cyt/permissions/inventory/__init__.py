"""Inventory adapters for permissions CLI."""

from cyt.permissions.inventory.mcp import (
    McpServerInventoryItem,
    McpServerSource,
    McpToolInventoryItem,
    list_mcp_servers,
    list_mcp_tools_for_server,
    load_mcp_server_names,
    load_mcp_server_sources,
)
from cyt.permissions.inventory.skills import (
    SkillInventoryItem,
    enumerate_skill_names,
    list_skills,
    skill_policy_name_from_path,
)

__all__ = [
    "McpServerInventoryItem",
    "McpServerSource",
    "McpToolInventoryItem",
    "SkillInventoryItem",
    "enumerate_skill_names",
    "list_mcp_servers",
    "list_mcp_tools_for_server",
    "list_skills",
    "load_mcp_server_names",
    "load_mcp_server_sources",
    "skill_policy_name_from_path",
]
