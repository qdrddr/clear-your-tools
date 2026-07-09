"""On-disk cache for raw executor tool catalogs."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXECUTOR_CATALOG_CACHE_DIR = Path("~/.config/cyt/cache/executor-catalog")


def normalize_executor_url_slug(url: str, *, token_var: str | None = None) -> str:
    """Map executor URL to a human-readable cache filename slug."""
    normalized = url.strip().rstrip("/")
    slug = re.sub(r"[^A-Za-z0-9.-]", "_", normalized)
    if token_var:
        slug = f"{slug}__{token_var}"
    return slug


def executor_catalog_cache_dir() -> Path:
    return _EXECUTOR_CATALOG_CACHE_DIR.expanduser()


def _canonical_tool_entry(tool: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": str(tool.get("name") or "")}
    if "description" in tool and tool["description"] is not None:
        entry["description"] = str(tool["description"])
    schema = tool.get("input_schema") or tool.get("inputSchema")
    if isinstance(schema, dict):
        entry["input_schema"] = schema
    else:
        entry["input_schema"] = {}
    return entry


def raw_catalog_content_hash(tools: list[dict[str, Any]]) -> str:
    """Stable sha256 over sorted tool name/description/input_schema (no policy fingerprint)."""
    canonical = [_canonical_tool_entry(tool) for tool in tools]
    canonical.sort(key=lambda item: item["name"])
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def raw_executor_mcp_content_hash(executor: dict[str, Any] | None) -> str:
    """Stable sha256 over the MCP transport cache under the ``executor`` root key."""
    if not executor:
        return ""
    payload = json.dumps(executor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _catalog_path(slug: str) -> Path:
    return executor_catalog_cache_dir() / f"{slug}.json"


def read_disk_catalog(slug: str) -> dict[str, Any] | None:
    """Return parsed envelope or ``None`` when missing/invalid."""
    path = _catalog_path(slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("executor catalog disk read failed for %s: %s", slug, exc)
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
    executor_url: str,
    tools: list[dict[str, Any]],
    content_hash: str,
    executor: dict[str, Any] | None = None,
) -> str:
    """Persist catalog envelope; skip write when tools + executor MCP hashes are unchanged.

    ``executor`` holds MCP transport responses (``tools/list`` + ``skills(execute)``).
    When ``executor`` is omitted, any existing ``executor`` block on disk is preserved.
    """
    existing = read_disk_catalog(slug)
    existing_executor = None
    if existing is not None:
        raw_existing = existing.get("executor")
        if isinstance(raw_existing, dict):
            existing_executor = raw_existing

    next_executor = executor if executor is not None else existing_executor
    next_executor_hash = raw_executor_mcp_content_hash(next_executor)
    existing_executor_hash = ""
    if existing is not None:
        existing_executor_hash = str(existing.get("executor_content_hash") or "")
        if not existing_executor_hash:
            existing_executor_hash = raw_executor_mcp_content_hash(existing_executor)

    tools_unchanged = existing is not None and existing.get("catalog_content_hash") == content_hash
    executor_unchanged = existing_executor_hash == next_executor_hash
    if tools_unchanged and executor_unchanged:
        logger.info(
            "executor catalog disk_write_skipped slug=%s catalog_content_hash=%s",
            slug,
            content_hash[:12],
        )
        return "disk_write_skipped"

    cache_dir = executor_catalog_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _catalog_path(slug)
    tmp_path = path.with_suffix(".json.tmp")
    envelope: dict[str, Any] = {
        "executor_url": executor_url,
        "catalog_content_hash": content_hash,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "tool_count": len(tools),
        "tools": tools,
    }
    if next_executor is not None:
        envelope["executor"] = next_executor
        envelope["executor_content_hash"] = next_executor_hash
    data = json.dumps(envelope, ensure_ascii=False, indent=2)
    tmp_path.write_text(data, encoding="utf-8")
    tmp_path.replace(path)
    action = "disk_write_updated" if existing is not None else "disk_write_created"
    logger.info(
        "executor catalog %s slug=%s catalog_content_hash=%s tool_count=%d executor=%s",
        action,
        slug,
        content_hash[:12],
        len(tools),
        "yes" if next_executor is not None else "no",
    )
    return action
