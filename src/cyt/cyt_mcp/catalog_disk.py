"""On-disk cache for cyt-mcp tool catalogs (hook daemon only)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CYT_MCP_CATALOG_CACHE_DIR = Path("~/.config/cyt/cache/cyt-mcp-catalog")


def normalize_cyt_mcp_agent_slug(agent: str) -> str:
    normalized = str(agent or "cursor").strip() or "cursor"
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", normalized)
    return slug


def cyt_mcp_catalog_cache_dir() -> Path:
    return _CYT_MCP_CATALOG_CACHE_DIR.expanduser()


def _canonical_tool_entry(tool: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
    }
    if "description" in tool and tool["description"] is not None:
        entry["description"] = str(tool["description"])
    schema = tool.get("input_schema") or tool.get("inputSchema")
    if isinstance(schema, dict):
        entry["input_schema"] = schema
    else:
        entry["input_schema"] = {}
    return entry


def raw_catalog_content_hash(tools: list[dict[str, Any]]) -> str:
    canonical = [_canonical_tool_entry(tool) for tool in tools]
    canonical.sort(key=lambda item: item["name"])
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _catalog_path(slug: str) -> Path:
    return cyt_mcp_catalog_cache_dir() / f"{slug}.json"


def read_disk_catalog(slug: str) -> dict[str, Any] | None:
    path = _catalog_path(slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("cyt-mcp catalog disk read failed for %s: %s", slug, exc)
        return None
    if not isinstance(payload, dict):
        return None
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return None
    return payload


def write_disk_catalog(
    slug: str,
    *,
    agent: str,
    tools: list[dict[str, Any]],
    content_hash: str,
) -> str:
    """Persist catalog envelope; skip write when unchanged."""
    existing = read_disk_catalog(slug)
    if existing is not None and existing.get("catalog_content_hash") == content_hash:
        logger.info(
            "cyt-mcp catalog disk_write_skipped slug=%s catalog_content_hash=%s",
            slug,
            content_hash[:12],
        )
        return "disk_write_skipped"

    cache_dir = cyt_mcp_catalog_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _catalog_path(slug)
    tmp_path = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
    envelope: dict[str, Any] = {
        "agent": agent,
        "catalog_content_hash": content_hash,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "tool_count": len(tools),
        "tools": tools,
    }
    data = json.dumps(envelope, ensure_ascii=False, indent=2)
    replaced = False
    try:
        tmp_path.write_text(data, encoding="utf-8")
        tmp_path.replace(path)
        replaced = True
    finally:
        if not replaced:
            tmp_path.unlink(missing_ok=True)
    action = "disk_write_updated" if existing is not None else "disk_write_created"
    logger.info(
        "cyt-mcp catalog %s slug=%s catalog_content_hash=%s tool_count=%d",
        action,
        slug,
        content_hash[:12],
        len(tools),
    )
    return action
