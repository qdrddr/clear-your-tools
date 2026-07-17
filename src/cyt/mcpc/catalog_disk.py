"""On-disk cache for MCPC tool catalogs."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCPC_CATALOG_CACHE_DIR = Path("~/.config/cyt/cache/mcpc-catalog")


def normalize_mcpc_executable_slug(executable: str) -> str:
    """Map mcpc executable path to a cache filename slug."""
    normalized = str(executable or "mcpc").strip() or "mcpc"
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", normalized)
    return slug


def mcpc_catalog_cache_dir() -> Path:
    return _MCPC_CATALOG_CACHE_DIR.expanduser()


def _canonical_tool_entry(tool: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
        "mcpc_session": str(tool.get("mcpc_session") or ""),
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
    """Stable sha256 over sorted tool entries."""
    canonical = [_canonical_tool_entry(tool) for tool in tools]
    canonical.sort(key=lambda item: (item["mcpc_session"], item["name"]))
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def raw_sessions_health_hash(sessions_health: dict[str, Any] | None) -> str:
    if not sessions_health:
        return ""
    payload = json.dumps(
        sessions_health,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _catalog_path(slug: str) -> Path:
    return mcpc_catalog_cache_dir() / f"{slug}.json"


def read_disk_catalog(slug: str) -> dict[str, Any] | None:
    path = _catalog_path(slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("mcpc catalog disk read failed for %s: %s", slug, exc)
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
    mcpc_executable: str,
    tools: list[dict[str, Any]],
    content_hash: str,
    sessions: dict[str, Any] | None = None,
    sessions_health: dict[str, Any] | None = None,
) -> str:
    """Persist catalog envelope; skip write when unchanged."""
    existing = read_disk_catalog(slug)
    existing_health_hash = ""
    if existing is not None:
        existing_health_hash = str(existing.get("sessions_health_hash") or "")
        if not existing_health_hash:
            existing_health_hash = raw_sessions_health_hash(existing.get("sessions_health"))
    next_health_hash = raw_sessions_health_hash(sessions_health)
    tools_unchanged = existing is not None and existing.get("catalog_content_hash") == content_hash
    health_unchanged = existing_health_hash == next_health_hash
    if tools_unchanged and health_unchanged:
        logger.info(
            "mcpc catalog disk_write_skipped slug=%s catalog_content_hash=%s",
            slug,
            content_hash[:12],
        )
        return "disk_write_skipped"

    cache_dir = mcpc_catalog_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _catalog_path(slug)
    tmp_path = path.with_suffix(".json.tmp")
    envelope: dict[str, Any] = {
        "mcpc_executable": mcpc_executable,
        "catalog_content_hash": content_hash,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "tool_count": len(tools),
        "tools": tools,
    }
    if sessions is not None:
        envelope["sessions"] = sessions
    if sessions_health is not None:
        envelope["sessions_health"] = sessions_health
        envelope["sessions_health_hash"] = next_health_hash
    data = json.dumps(envelope, ensure_ascii=False, indent=2)
    tmp_path.write_text(data, encoding="utf-8")
    tmp_path.replace(path)
    action = "disk_write_updated" if existing is not None else "disk_write_created"
    logger.info(
        "mcpc catalog %s slug=%s catalog_content_hash=%s tool_count=%d",
        action,
        slug,
        content_hash[:12],
        len(tools),
    )
    return action
