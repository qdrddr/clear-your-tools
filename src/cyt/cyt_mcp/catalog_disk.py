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


def _legacy_catalog_path(slug: str) -> Path:
    return cyt_mcp_catalog_cache_dir() / f"{slug}.json"


def _backend_ref_path(scope_slug: str) -> Path:
    return cyt_mcp_catalog_cache_dir() / "backends" / f"{scope_slug}.json"


def _by_hash_path(content_hash: str) -> Path:
    return cyt_mcp_catalog_cache_dir() / "by-hash" / f"{content_hash}.json"


def _read_backend_ref(scope_slug: str) -> dict[str, Any] | None:
    path = _backend_ref_path(scope_slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_by_hash_envelope(content_hash: str) -> dict[str, Any] | None:
    path = _by_hash_path(content_hash)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("cyt-mcp catalog by-hash read failed for %s: %s", content_hash[:12], exc)
        return None
    if not isinstance(payload, dict):
        return None
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return None
    return payload


def read_disk_catalog(slug: str) -> dict[str, Any] | None:
    ref = _read_backend_ref(slug)
    if ref is not None:
        content_hash = str(ref.get("catalog_content_hash") or "").strip()
        if content_hash:
            envelope = _read_by_hash_envelope(content_hash)
            if envelope is not None:
                return envelope

    legacy = _legacy_catalog_path(slug)
    if not legacy.is_file():
        return None
    try:
        payload = json.loads(legacy.read_text(encoding="utf-8"))
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
    """Persist catalog envelope via by-hash content + backends scope ref."""
    ref = _read_backend_ref(slug)
    if ref is not None and str(ref.get("catalog_content_hash") or "") == content_hash:
        logger.info(
            "cyt-mcp catalog disk_write_skipped slug=%s catalog_content_hash=%s",
            slug,
            content_hash[:12],
        )
        return "disk_write_skipped"

    legacy = _legacy_catalog_path(slug)
    if ref is None and legacy.is_file():
        try:
            existing = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            existing = None
        if isinstance(existing, dict) and existing.get("catalog_content_hash") == content_hash:
            logger.info(
                "cyt-mcp catalog disk_write_skipped slug=%s catalog_content_hash=%s",
                slug,
                content_hash[:12],
            )
            return "disk_write_skipped"

    cache_dir = cyt_mcp_catalog_cache_dir()
    (cache_dir / "by-hash").mkdir(parents=True, exist_ok=True)
    (cache_dir / "backends").mkdir(parents=True, exist_ok=True)

    by_hash_path = _by_hash_path(content_hash)
    if not by_hash_path.is_file():
        envelope: dict[str, Any] = {
            "agent": agent,
            "catalog_content_hash": content_hash,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "tool_count": len(tools),
            "tools": tools,
        }
        tmp_path = by_hash_path.with_name(f"{by_hash_path.stem}.{uuid.uuid4().hex}.json.tmp")
        replaced = False
        try:
            tmp_path.write_text(
                json.dumps(envelope, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(by_hash_path)
            replaced = True
        finally:
            if not replaced:
                tmp_path.unlink(missing_ok=True)

    ref_path = _backend_ref_path(slug)
    ref_payload = {
        "agent": agent,
        "scope_slug": slug,
        "catalog_content_hash": content_hash,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    tmp_ref = ref_path.with_name(f"{ref_path.stem}.{uuid.uuid4().hex}.json.tmp")
    ref_replaced = False
    try:
        tmp_ref.write_text(json.dumps(ref_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_ref.replace(ref_path)
        ref_replaced = True
    finally:
        if not ref_replaced:
            tmp_ref.unlink(missing_ok=True)

    action = "disk_write_updated" if ref is not None or legacy.is_file() else "disk_write_created"
    logger.info(
        "cyt-mcp catalog %s slug=%s catalog_content_hash=%s tool_count=%d",
        action,
        slug,
        content_hash[:12],
        len(tools),
    )
    return action


def scope_config_fingerprint(*paths: Path, version: str = "") -> str:
    """Content-addressed fingerprint for aggregator + server-def paths (per install scope)."""
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(path.read_bytes())
    if version:
        digest.update(version.encode("utf-8"))
    return digest.hexdigest()


def merged_hook_catalog_slug(global_fingerprint: str, workspace_fingerprint: str | None) -> str:
    """Composite slug for hook injection merged catalog cache entries."""
    if not workspace_fingerprint:
        return global_fingerprint
    combined = f"{global_fingerprint}:{workspace_fingerprint}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
