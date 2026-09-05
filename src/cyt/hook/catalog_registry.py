"""In-memory catalog registry for cyt-mcp push registrations (hook daemon)."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash
from cyt_mcp.catalog import merge_catalog_payloads

logger = logging.getLogger(__name__)

CatalogScope = Literal["global", "workspace"]

REGISTRY_TTL_SECONDS = 10.0
REGISTRY_SNAPSHOT_DIR = Path("~/.config/cyt/cache/catalog-registry").expanduser()
REGISTRY_SNAPSHOT_FILE = REGISTRY_SNAPSHOT_DIR / "registrations.json"

_registry_lock = threading.Lock()
_registrations: dict[tuple[str, str, str], _CatalogRegistration] = {}


class RegisterStatus(StrEnum):
    STORED = "stored"
    UNCHANGED = "unchanged"
    UNKNOWN_HASH = "unknown_hash"
    INVALID = "invalid"


@dataclass
class RegisterResult:
    status: RegisterStatus
    http_status: int
    message: str = ""


@dataclass
class _CatalogRegistration:
    agent: str
    scope: CatalogScope
    workspace_root: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    content_hash: str = ""
    instance_id: str = ""
    registered_at: float = 0.0
    last_seen_at: float = 0.0
    stale: bool = False


def _normalize_agent(raw: object) -> str:
    text = str(raw or "cursor").strip().lower()
    return text if text in {"cursor", "claude", "codex"} else "cursor"


def _normalize_scope(raw: object) -> CatalogScope | None:
    text = str(raw or "").strip().lower()
    if text == "global":
        return "global"
    if text == "workspace":
        return "workspace"
    return None


def normalize_registry_workspace_path(raw: object) -> str | None:
    """Return normalized absolute workspace path, or None for global scope."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from cyt_client.rules_file import normalize_workspace_path_string

    normalized = normalize_workspace_path_string(text)
    path = Path(normalized).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return str(resolved) if resolved.is_dir() else None


def _registry_key(
    agent: str,
    scope: CatalogScope,
    workspace_root: str | None,
) -> tuple[str, str, str]:
    ws = "" if scope == "global" else (workspace_root or "")
    return (agent, scope, ws)


def _normalize_tools(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tools: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            tools.append(copy.deepcopy(item))
    return tools


def _registration_to_dict(entry: _CatalogRegistration) -> dict[str, Any]:
    return {
        "agent": entry.agent,
        "scope": entry.scope,
        "workspace_root": entry.workspace_root or None,
        "tools": entry.tools,
        "content_hash": entry.content_hash,
        "instance_id": entry.instance_id,
        "registered_at": entry.registered_at,
        "last_seen_at": entry.last_seen_at,
        "stale": entry.stale,
    }


def _entry_from_dict(raw: dict[str, Any]) -> _CatalogRegistration | None:
    agent = _normalize_agent(raw.get("agent"))
    scope = _normalize_scope(raw.get("scope"))
    if scope is None:
        return None
    ws_raw = raw.get("workspace_root")
    workspace = normalize_registry_workspace_path(ws_raw) if scope == "workspace" else None
    if scope == "workspace" and workspace is None:
        return None
    tools = _normalize_tools(raw.get("tools"))
    content_hash = str(raw.get("content_hash") or "")
    if not content_hash and tools:
        content_hash = raw_catalog_content_hash(tools)
    return _CatalogRegistration(
        agent=agent,
        scope=scope,
        workspace_root="" if scope == "global" else str(workspace),
        tools=tools,
        content_hash=content_hash,
        instance_id=str(raw.get("instance_id") or ""),
        registered_at=float(raw.get("registered_at") or 0.0),
        last_seen_at=float(raw.get("last_seen_at") or 0.0),
        stale=bool(raw.get("stale", True)),
    )


def _is_entry_live(entry: _CatalogRegistration, *, now: float | None = None) -> bool:
    if entry.stale:
        return False
    if not entry.tools or not entry.content_hash:
        return False
    current = now if now is not None else time.monotonic()
    return current - entry.last_seen_at <= REGISTRY_TTL_SECONDS


def _get_entry(key: tuple[str, str, str]) -> _CatalogRegistration | None:
    with _registry_lock:
        return _registrations.get(key)


def _upsert_entry(entry: _CatalogRegistration) -> None:
    key = _registry_key(entry.agent, entry.scope, entry.workspace_root or None)
    with _registry_lock:
        _registrations[key] = entry
    _schedule_snapshot_write()


def _remove_entry(key: tuple[str, str, str], *, instance_id: str | None = None) -> bool:
    with _registry_lock:
        existing = _registrations.get(key)
        if existing is None:
            return False
        if instance_id and existing.instance_id != instance_id:
            return False
        del _registrations[key]
    _schedule_snapshot_write()
    return True


_snapshot_write_lock = threading.Lock()
_snapshot_pending = False


def _schedule_snapshot_write() -> None:
    global _snapshot_pending
    with _snapshot_write_lock:
        if _snapshot_pending:
            return
        _snapshot_pending = True
    thread = threading.Thread(
        target=_write_snapshot_async,
        name="cyt-catalog-registry-snapshot",
        daemon=True,
    )
    thread.start()


def _write_snapshot_async() -> None:
    global _snapshot_pending
    try:
        with _registry_lock:
            payload = [_registration_to_dict(entry) for entry in _registrations.values()]
        REGISTRY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = REGISTRY_SNAPSHOT_DIR / f"registrations.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(REGISTRY_SNAPSHOT_FILE)
    except OSError as exc:
        logger.warning("catalog registry snapshot write failed: %s", exc)
    finally:
        with _snapshot_write_lock:
            _snapshot_pending = False


def load_catalog_registry_from_disk(*, mark_stale: bool = True) -> int:
    """Load registry snapshot; return count loaded."""
    if not REGISTRY_SNAPSHOT_FILE.is_file():
        return 0
    try:
        raw = json.loads(REGISTRY_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("catalog registry snapshot read failed: %s", exc)
        return 0
    if not isinstance(raw, list):
        return 0
    loaded = 0
    now = time.monotonic()
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = _entry_from_dict(item)
        if entry is None:
            continue
        if mark_stale:
            entry.stale = True
        entry.last_seen_at = now
        key = _registry_key(entry.agent, entry.scope, entry.workspace_root or None)
        with _registry_lock:
            _registrations[key] = entry
        loaded += 1
    logger.info("catalog registry loaded %d entries from disk (stale=%s)", loaded, mark_stale)
    return loaded


def clear_catalog_registry() -> None:
    with _registry_lock:
        _registrations.clear()


def touch_heartbeat(
    agent: str,
    scope: CatalogScope,
    workspace_root: str | Path | None,
    *,
    content_hash: str,
    instance_id: str = "",
) -> RegisterResult:
    """Hash-only heartbeat for an existing registration."""
    payload: dict[str, Any] = {
        "agent": agent,
        "scope": scope,
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
        "content_hash": content_hash,
        "instance_id": instance_id,
    }
    return register_catalog(payload)


def _register_hash_only(
    existing: _CatalogRegistration | None,
    *,
    content_hash: str,
    instance_id: str,
) -> RegisterResult:
    if existing is None or not existing.tools:
        return RegisterResult(RegisterStatus.UNKNOWN_HASH, 404, "registration not found")
    if existing.content_hash != content_hash:
        return RegisterResult(RegisterStatus.UNKNOWN_HASH, 404, "hash mismatch")
    existing.last_seen_at = time.monotonic()
    existing.stale = False
    if instance_id:
        existing.instance_id = instance_id
    _upsert_entry(existing)
    return RegisterResult(RegisterStatus.UNCHANGED, 204)


def _register_full_tools(
    existing: _CatalogRegistration | None,
    *,
    agent: str,
    scope: CatalogScope,
    workspace_root: str | None,
    tools: list[dict[str, Any]],
    content_hash: str,
    instance_id: str,
) -> RegisterResult:
    if (
        existing is not None
        and existing.content_hash == content_hash
        and existing.tools
        and not existing.stale
    ):
        existing.last_seen_at = time.monotonic()
        if instance_id:
            existing.instance_id = instance_id
        _upsert_entry(existing)
        return RegisterResult(RegisterStatus.UNCHANGED, 204)

    now = time.monotonic()
    entry = _CatalogRegistration(
        agent=agent,
        scope=scope,
        workspace_root="" if scope == "global" else str(workspace_root),
        tools=tools,
        content_hash=content_hash,
        instance_id=instance_id,
        registered_at=now,
        last_seen_at=now,
        stale=False,
    )
    _upsert_entry(entry)
    return RegisterResult(RegisterStatus.STORED, 200)


def register_catalog(payload: dict[str, Any]) -> RegisterResult:
    agent = _normalize_agent(payload.get("agent"))
    scope = _normalize_scope(payload.get("scope"))
    if scope is None:
        return RegisterResult(RegisterStatus.INVALID, 400, "invalid scope")

    workspace_root: str | None
    if scope == "global":
        workspace_root = None
    else:
        workspace_root = normalize_registry_workspace_path(payload.get("workspace_root"))
        if workspace_root is None:
            return RegisterResult(RegisterStatus.INVALID, 400, "invalid workspace_root")

    content_hash = str(payload.get("content_hash") or "").strip()
    instance_id = str(payload.get("instance_id") or "").strip()
    if not content_hash:
        return RegisterResult(RegisterStatus.INVALID, 400, "content_hash required")

    key = _registry_key(agent, scope, workspace_root)
    tools_raw = payload.get("tools")
    has_tools = isinstance(tools_raw, list) and len(tools_raw) > 0

    with _registry_lock:
        existing = _registrations.get(key)

    if not has_tools:
        return _register_hash_only(existing, content_hash=content_hash, instance_id=instance_id)

    tools = _normalize_tools(tools_raw)
    computed_hash = raw_catalog_content_hash(tools)
    if content_hash != computed_hash:
        content_hash = computed_hash

    return _register_full_tools(
        existing,
        agent=agent,
        scope=scope,
        workspace_root=workspace_root,
        tools=tools,
        content_hash=content_hash,
        instance_id=instance_id,
    )


def deregister_catalog(payload: dict[str, Any]) -> bool:
    agent = _normalize_agent(payload.get("agent"))
    scope = _normalize_scope(payload.get("scope"))
    if scope is None:
        return False
    if scope == "global":
        workspace_root = None
    else:
        workspace_root = normalize_registry_workspace_path(payload.get("workspace_root"))
        if workspace_root is None:
            return False
    instance_id = str(payload.get("instance_id") or "").strip() or None
    key = _registry_key(agent, scope, workspace_root)
    return _remove_entry(key, instance_id=instance_id)


def list_catalog_registrations() -> list[dict[str, Any]]:
    with _registry_lock:
        return [_registration_to_dict(entry) for entry in _registrations.values()]


def _entry_tools(
    entry: _CatalogRegistration | None,
    *,
    allow_stale: bool = True,
) -> list[dict[str, Any]]:
    if entry is None or not entry.tools:
        return []
    if allow_stale:
        return copy.deepcopy(entry.tools)
    if _is_entry_live(entry):
        return copy.deepcopy(entry.tools)
    return []


def _stamp_catalog_scope(
    tools: list[dict[str, Any]],
    scope: CatalogScope,
) -> list[dict[str, Any]]:
    """Stamp ``cyt_catalog_scope`` on tools (registry ``global`` → canonical ``user``)."""
    cyt_scope = "workspace" if scope == "workspace" else "user"
    stamped: list[dict[str, Any]] = []
    for tool in tools:
        item = copy.deepcopy(tool)
        item["cyt_catalog_scope"] = cyt_scope
        stamped.append(item)
    return stamped


def merge_catalog_for_hook(
    agent: str,
    workspace_root: str | Path | None,
    *,
    allow_stale: bool = True,
) -> list[dict[str, Any]]:
    """Merge user-scoped + workspace cyt-mcp registrations for hook injection."""
    normalized_agent = _normalize_agent(agent)
    global_key = _registry_key(normalized_agent, "global", None)
    global_entry = _get_entry(global_key)
    global_tools = _stamp_catalog_scope(
        _entry_tools(global_entry, allow_stale=allow_stale),
        "global",
    )

    ws_path: str | None = None
    if workspace_root is not None:
        ws_path = normalize_registry_workspace_path(str(workspace_root))

    workspace_tools: list[dict[str, Any]] = []
    if ws_path:
        ws_key = _registry_key(normalized_agent, "workspace", ws_path)
        ws_entry = _get_entry(ws_key)
        workspace_tools = _stamp_catalog_scope(
            _entry_tools(ws_entry, allow_stale=allow_stale),
            "workspace",
        )

    if not global_tools and not workspace_tools:
        return []

    if not workspace_tools:
        return global_tools

    if not global_tools:
        return workspace_tools

    merged = merge_catalog_payloads(
        {"agent": normalized_agent, "tools": global_tools},
        {"agent": normalized_agent, "tools": workspace_tools},
    )
    tools = merged.get("tools")
    return copy.deepcopy(tools) if isinstance(tools, list) else []


def prune_expired_registrations() -> int:
    """Remove entries that exceeded TTL and are not stale fallbacks."""
    now = time.monotonic()
    removed = 0
    with _registry_lock:
        keys_to_remove = [
            key
            for key, entry in _registrations.items()
            if not entry.stale and now - entry.last_seen_at > REGISTRY_TTL_SECONDS
        ]
        for key in keys_to_remove:
            del _registrations[key]
            removed += 1
    if removed:
        _schedule_snapshot_write()
    return removed
