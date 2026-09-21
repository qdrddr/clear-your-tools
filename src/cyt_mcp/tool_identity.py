"""Deterministic cyt-mcp wire name ↔ backend (server_key, tool_name) mapping."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME


@dataclass(frozen=True)
class CytMcpToolIdentity:
    wire_name: str
    server_key: str
    backend_tool_name: str


def wire_name_for(server_key: str, backend_tool_name: str) -> str:
    return f"{server_key}_{backend_tool_name}"


def server_key_candidates_from_wire_name(wire_name: str) -> list[str]:
    """Derive possible MCP server keys from a catalog wire name (longest prefix first)."""
    name = str(wire_name or "").strip()
    if "_" not in name:
        return []
    parts = name.split("_")
    return ["_".join(parts[:index]) for index in range(1, len(parts))]


def collect_server_keys_from_tools(tools: Sequence[Any]) -> list[str]:
    """Collect explicit and wire-name-derived server keys for identity enrichment."""
    keys: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            continue
        server_key = item.get("server_key")
        if isinstance(server_key, str) and server_key.strip():
            keys.add(server_key.strip())
        wire = str(item.get("name") or "").strip()
        keys.update(server_key_candidates_from_wire_name(wire))
    return sorted(keys, key=len, reverse=True)


def server_keys_for_enrichment(tools: Sequence[Any]) -> list[str]:
    """Server keys for ``enrich_tool_identity`` (configured keys + wire-name hints)."""
    keys = set(collect_server_keys_from_tools(tools))
    try:
        from cyt_mcp.config import load_known_mcp_server_keys

        keys.update(load_known_mcp_server_keys())
    except Exception:
        pass
    return sorted(keys, key=len, reverse=True)


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


def is_known_server_key(server_key: str, server_keys: list[str]) -> bool:
    """True when *server_key* appears in the configured MCP server key list."""
    key = str(server_key or "").strip()
    if not key:
        return False
    return key in {str(item).strip() for item in server_keys if str(item).strip()}


def is_canonical_schema_identity(
    mcp_server: str,
    tool_name: str,
    server_keys: list[str],
) -> bool:
    """True when stored (mcp_server, tool_name) matches wire-name split for known server keys."""
    server = str(mcp_server or "").strip()
    bare = str(tool_name or "").strip()
    if not server or not bare or not server_keys:
        return False
    if not is_known_server_key(server, server_keys):
        return False
    wire = wire_name_for(server, bare)
    identity = split_wire_name(wire, server_keys)
    if identity is None:
        return wire == bare or wire == server
    return identity.server_key == server and identity.backend_tool_name == bare


def canonical_backend_identity(
    tool: dict[str, Any],
    server_keys: list[str],
) -> tuple[str, str]:
    """Return authoritative backend identity using wire-name split when possible."""
    wire = str(tool.get("name") or "").strip()
    explicit_server = str(tool.get("server_key") or tool.get("mcp_server") or "").strip()
    explicit_bare = str(tool.get("tool_name") or "").strip()
    if not explicit_bare and explicit_server and wire:
        explicit_bare = _bare_tool_name_from_wire(server=explicit_server, wire=wire)

    split_identity = split_wire_name(wire, server_keys) if wire and server_keys else None
    if split_identity is not None:
        return split_identity.server_key, split_identity.backend_tool_name

    if (
        explicit_server
        and explicit_bare
        and is_known_server_key(explicit_server, server_keys)
        and (
            not wire
            or wire == wire_name_for(explicit_server, explicit_bare)
            or wire == explicit_bare
        )
    ):
        return explicit_server, explicit_bare

    return "unknown", wire or "unknown"


def tool_name_allowed_for_servers(tool_name: str, server_keys: Sequence[str]) -> bool:
    name = str(tool_name or "").strip()
    if not name or name == MCP_WIRE_SEARCH_TOOL_NAME:
        return True
    for key in sorted(server_keys, key=len, reverse=True):
        prefix = f"{key}_"
        if name.startswith(prefix):
            return True
    return False
