"""Deterministic cyt-mcp wire name ↔ backend (server_key, tool_name) mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CytMcpToolIdentity:
    wire_name: str
    server_key: str
    backend_tool_name: str


def wire_name_for(server_key: str, backend_tool_name: str) -> str:
    return f"{server_key}_{backend_tool_name}"


def split_wire_name(wire_name: str, server_keys: list[str]) -> CytMcpToolIdentity | None:
    """Resolve frontend wire name using configured server keys (longest prefix wins)."""
    name = str(wire_name or "").strip()
    if not name or not server_keys:
        return None
    for server_key in sorted(
        {str(key).strip() for key in server_keys if str(key).strip()},
        key=len,
        reverse=True,
    ):
        prefix = f"{server_key}_"
        if not name.startswith(prefix):
            continue
        backend_tool_name = name[len(prefix) :].strip()
        if backend_tool_name:
            return CytMcpToolIdentity(
                wire_name=name,
                server_key=server_key,
                backend_tool_name=backend_tool_name,
            )
    return None


def enrich_tool_identity(tool: dict[str, Any], server_keys: list[str]) -> dict[str, Any]:
    """Attach server_key and backend tool_name; normalize wire name from the mapping."""
    enriched = dict(tool)
    server = str(enriched.get("server_key") or enriched.get("mcp_server") or "").strip()
    bare = str(enriched.get("tool_name") or "").strip()
    wire = str(enriched.get("name") or "").strip()

    if server and bare:
        enriched["server_key"] = server
        enriched["tool_name"] = bare
        enriched["name"] = wire_name_for(server, bare)
        return enriched

    identity = split_wire_name(wire, server_keys)
    if identity is None:
        return enriched

    enriched["name"] = identity.wire_name
    enriched["server_key"] = identity.server_key
    enriched["tool_name"] = identity.backend_tool_name
    return enriched


def _bare_tool_name_from_wire(*, server: str, wire: str) -> str:
    """Infer backend tool name when session log omitted tool_name (wire == bare)."""
    text = wire.strip()
    if not text:
        return ""
    prefix = f"{server}_"
    if text.startswith(prefix):
        suffix = text[len(prefix) :].strip()
        if suffix:
            return suffix
    return text


def resolve_backend_identity(tool: dict[str, Any]) -> tuple[str, str]:
    """Return backend (server_key, tool_name) from explicit catalog fields only."""
    server = str(tool.get("server_key") or tool.get("mcp_server") or "").strip()
    bare = str(tool.get("tool_name") or "").strip()
    wire = str(tool.get("name") or "").strip()
    if not bare and server and wire:
        bare = _bare_tool_name_from_wire(server=server, wire=wire)
    if server and bare:
        return server, bare
    return "unknown", wire or "unknown"
