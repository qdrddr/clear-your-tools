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

logger = logging.getLogger(__name__)

CatalogScope = Literal["workspace"]
CatalogLayer = Literal["usr", "ws"]

REGISTRY_TTL_SECONDS = 10.0
REGISTRY_SNAPSHOT_DIR = Path("~/.config/cyt/cache/catalog-registry").expanduser()
REGISTRY_SNAPSHOT_FILE = REGISTRY_SNAPSHOT_DIR / "registrations.json"

_registry_lock = threading.Lock()
_registrations: dict[tuple[str, str, str, str], _CatalogRegistration] = {}


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
    permissions_revision: int = 0


@dataclass
class _CatalogRegistration:
    agent: str
    scope: CatalogScope
    workspace_root: str
    catalog_layer: CatalogLayer
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
    if text in {"global", "user"}:
        return None
    if text == "workspace":
        return "workspace"
    return None


def _normalize_catalog_layer(raw: object) -> CatalogLayer | None:
    text = str(raw or "").strip().lower()
    if text in {"usr", "user"}:
        return "usr"
    if text in {"ws", "workspace"}:
        return "ws"
    return None


def normalize_registry_workspace_path(raw: object) -> str | None:
    """Return normalized absolute workspace path."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from cyt.hook.workspace_resolution import (
        WorkspacePathNotAbsoluteError,
        require_absolute_workspace_dir,
    )

    try:
        resolved = require_absolute_workspace_dir(text, label="workspace")
    except (WorkspacePathNotAbsoluteError, ValueError, OSError):
        return None
    return str(resolved)


def _registry_key(
    agent: str,
    workspace_root: str | None,
    catalog_layer: CatalogLayer,
) -> tuple[str, str, str, str]:
    ws = workspace_root or ""
    return (agent, "workspace", ws, catalog_layer)


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
        "catalog_layer": entry.catalog_layer,
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
    catalog_layer = _normalize_catalog_layer(raw.get("catalog_layer"))
    if catalog_layer is None:
        return None
    workspace = normalize_registry_workspace_path(raw.get("workspace_root"))
    if workspace is None:
        return None
    tools = _normalize_tools(raw.get("tools"))
    content_hash = str(raw.get("content_hash") or "")
    if not content_hash and tools:
        content_hash = raw_catalog_content_hash(tools)
    return _CatalogRegistration(
        agent=agent,
        scope=scope,
        workspace_root=str(workspace),
        catalog_layer=catalog_layer,
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


def _get_entry(key: tuple[str, str, str, str]) -> _CatalogRegistration | None:
    with _registry_lock:
        return _registrations.get(key)


def _upsert_entry(entry: _CatalogRegistration) -> None:
    key = _registry_key(entry.agent, entry.workspace_root or None, entry.catalog_layer)
    with _registry_lock:
        _registrations[key] = entry
    _schedule_snapshot_write()


def _remove_entry(key: tuple[str, str, str, str], *, instance_id: str | None = None) -> bool:
    global _snapshot_generation
    if not _snapshot_idle.wait(timeout=5.0):
        logger.warning(
            "catalog registry snapshot flush timed out waiting for async write",
        )
    with _snapshot_write_lock:
        _snapshot_generation += 1
        generation = _snapshot_generation
    with _snapshot_file_lock:
        with _registry_lock:
            existing = _registrations.get(key)
            if existing is None:
                return False
            if instance_id and existing.instance_id != instance_id:
                return False
            del _registrations[key]
            payload = [_registration_to_dict(entry) for entry in _registrations.values()]
        with _snapshot_write_lock:
            if generation == _snapshot_generation:
                try:
                    _write_registry_snapshot_file(payload)
                except OSError as exc:
                    logger.warning("catalog registry snapshot write failed: %s", exc)
    return True


_snapshot_write_lock = threading.Lock()
_snapshot_file_lock = threading.Lock()
_snapshot_pending = False
_snapshot_generation = 0
_snapshot_idle = threading.Event()
_snapshot_idle.set()


def _schedule_snapshot_write() -> None:
    global _snapshot_pending, _snapshot_generation
    with _snapshot_write_lock:
        _snapshot_generation += 1
        if _snapshot_pending:
            return
        _snapshot_pending = True
    thread = threading.Thread(
        target=_write_snapshot_async,
        name="cyt-catalog-registry-snapshot",
        daemon=True,
    )
    thread.start()


def _write_registry_snapshot_file(payload: list[dict[str, Any]]) -> None:
    REGISTRY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_SNAPSHOT_DIR / f"registrations.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(REGISTRY_SNAPSHOT_FILE)


def _registry_snapshot_payload_locked() -> list[dict[str, Any]]:
    with _registry_lock:
        return [_registration_to_dict(entry) for entry in _registrations.values()]


def _try_write_registry_snapshot(payload: list[dict[str, Any]], *, generation: int) -> bool:
    """Persist snapshot when ``generation`` is still current (check spans the write)."""
    with _snapshot_file_lock:
        with _snapshot_write_lock:
            if generation != _snapshot_generation:
                return False
            try:
                _write_registry_snapshot_file(payload)
            except OSError as exc:
                logger.warning("catalog registry snapshot write failed: %s", exc)
                return False
    return True


def _write_snapshot_async() -> None:
    global _snapshot_pending
    _snapshot_idle.clear()
    try:
        while True:
            with _snapshot_write_lock:
                generation = _snapshot_generation
            payload = _registry_snapshot_payload_locked()
            if not _try_write_registry_snapshot(payload, generation=generation):
                continue
            with _snapshot_write_lock:
                if generation == _snapshot_generation:
                    break
    finally:
        with _snapshot_write_lock:
            _snapshot_pending = False
        _snapshot_idle.set()


def _persist_registry_snapshot_payload(
    payload: list[dict[str, Any]],
    *,
    wait_timeout: float = 5.0,
) -> None:
    """Write an exact registry snapshot, waiting for in-flight async writers."""
    global _snapshot_generation
    if not _snapshot_idle.wait(timeout=wait_timeout):
        logger.warning(
            "catalog registry snapshot flush timed out waiting for async write",
        )
    with _snapshot_write_lock:
        _snapshot_generation += 1
        generation = _snapshot_generation
    while not _try_write_registry_snapshot(payload, generation=generation):
        with _snapshot_write_lock:
            _snapshot_generation += 1
            generation = _snapshot_generation


def _persist_registry_snapshot_blocking(*, wait_timeout: float = 5.0) -> None:
    """Write the current registry snapshot, waiting for in-flight async writers."""
    _persist_registry_snapshot_payload(
        _registry_snapshot_payload_locked(),
        wait_timeout=wait_timeout,
    )


def _compact_registry_snapshot_entries(raw: list[Any]) -> list[dict[str, Any]]:
    """Return canonical registration dicts, dropping legacy or invalid rows."""
    compact: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = _entry_from_dict(item)
        if entry is None:
            continue
        compact.append(_registration_to_dict(entry))
    return compact


def _registry_snapshot_needs_compaction(raw: list[Any], compact: list[dict[str, Any]]) -> bool:
    if len(compact) != len(raw):
        return True
    for item in raw:
        if not isinstance(item, dict):
            return True
        entry = _entry_from_dict(item)
        if entry is None:
            return True
        if item != _registration_to_dict(entry):
            return True
    return False


def _migrate_registry_snapshot_file(raw: list[Any]) -> list[dict[str, Any]]:
    """One-shot rewrite: persist only valid workspace-scoped registration rows."""
    compact = _compact_registry_snapshot_entries(raw)
    if not _registry_snapshot_needs_compaction(raw, compact):
        return compact
    try:
        _write_registry_snapshot_file(compact)
        logger.info(
            "catalog registry snapshot compacted (%d -> %d entries)",
            len(raw),
            len(compact),
        )
    except OSError as exc:
        logger.warning("catalog registry snapshot compaction failed: %s", exc)
    return compact


def load_catalog_registry_from_disk(*, mark_stale: bool = True) -> int:
    """Load registry snapshot; return count loaded. Compacts legacy rows on first read."""
    with _snapshot_file_lock:
        if not REGISTRY_SNAPSHOT_FILE.is_file():
            return 0
        try:
            raw = json.loads(REGISTRY_SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("catalog registry snapshot read failed: %s", exc)
            return 0
        if not isinstance(raw, list):
            return 0
        compact = _migrate_registry_snapshot_file(raw)
        loaded = 0
        now = time.monotonic()
        for item in compact:
            entry = _entry_from_dict(item)
            if entry is None:
                continue
            if mark_stale:
                entry.stale = True
            entry.last_seen_at = now
            key = _registry_key(entry.agent, entry.workspace_root or None, entry.catalog_layer)
            with _registry_lock:
                existing = _registrations.get(key)
                # Disk snapshots are stale fallbacks; never clobber a live in-process push.
                if existing is not None and not existing.stale:
                    continue
                _registrations[key] = entry
            loaded += 1
        logger.info("catalog registry loaded %d entries from disk (stale=%s)", loaded, mark_stale)
        return loaded


def clear_catalog_registry(*, purge_disk_snapshot: bool = True) -> None:
    with _registry_lock:
        _registrations.clear()
    if purge_disk_snapshot and REGISTRY_SNAPSHOT_FILE.is_file():
        try:
            REGISTRY_SNAPSHOT_FILE.unlink()
        except OSError as exc:
            logger.warning("catalog registry snapshot delete failed: %s", exc)


def touch_heartbeat(
    agent: str,
    scope: CatalogScope,
    workspace_root: str | Path | None,
    *,
    content_hash: str,
    instance_id: str = "",
    catalog_layer: CatalogLayer = "ws",
) -> RegisterResult:
    """Hash-only heartbeat for an existing registration."""
    payload: dict[str, Any] = {
        "agent": agent,
        "scope": scope,
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
        "content_hash": content_hash,
        "instance_id": instance_id,
        "catalog_layer": catalog_layer,
    }
    return register_catalog(payload)


def _permissions_revision_for(agent: str, workspace_root: str | None) -> int:
    if not workspace_root:
        return 0
    from cyt.hook.permissions_revision import get_permissions_revision

    return get_permissions_revision(agent, workspace_root)


def _register_hash_only(
    existing: _CatalogRegistration | None,
    *,
    content_hash: str,
    instance_id: str,
) -> RegisterResult:
    if existing is None or not existing.tools:
        return RegisterResult(RegisterStatus.UNKNOWN_HASH, 404, "registration not found")
    if existing.workspace_root:
        from cyt.hook.active_workspace import touch_active_workspace

        touch_active_workspace(existing.agent, existing.workspace_root)
    if existing.content_hash != content_hash:
        return RegisterResult(RegisterStatus.UNKNOWN_HASH, 404, "hash mismatch")
    existing.last_seen_at = time.monotonic()
    existing.stale = False
    if instance_id:
        existing.instance_id = instance_id
    _upsert_entry(existing)
    revision = _permissions_revision_for(existing.agent, existing.workspace_root)
    return RegisterResult(RegisterStatus.UNCHANGED, 204, permissions_revision=revision)


def _hydrate_hook_caches_from_registration(
    tools: list[dict[str, Any]],
    *,
    agent: str,
    workspace_root: str,
) -> None:
    """Populate hook-side cyt-mcp and master caches after a registry push."""
    if not tools:
        return
    try:
        from cyt.config import load_config, uses_cyt_mcp_tool_catalog
        from cyt.cyt_mcp.catalog import apply_fetched_catalog
        from cyt.hook.workspace_config import set_hook_workspace_in_config
        from cyt.tools.master_catalog import rebuild_master_catalog

        cfg = load_config()
        if not uses_cyt_mcp_tool_catalog(cfg):
            return
        cfg = set_hook_workspace_in_config(cfg, Path(workspace_root))
        merged = catalog_for_hook(agent, workspace_root, allow_stale=True)
        apply_fetched_catalog(cfg, merged if merged else tools)
        # Blocking: dual-layer pushes (ws then usr) must not lose the second layer when
        # a prior non-blocking rebuild is still in flight (common under pytest-xdist).
        rebuild_master_catalog(cfg, blocking=True)
    except Exception as exc:
        logger.warning("hook cache hydration after catalog register failed: %s", exc)


def _register_full_tools(
    existing: _CatalogRegistration | None,
    *,
    agent: str,
    scope: CatalogScope,
    workspace_root: str | None,
    catalog_layer: CatalogLayer,
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
        revision = _permissions_revision_for(agent, workspace_root)
        return RegisterResult(RegisterStatus.UNCHANGED, 204, permissions_revision=revision)

    now = time.monotonic()
    entry = _CatalogRegistration(
        agent=agent,
        scope="workspace",
        workspace_root=str(workspace_root),
        catalog_layer=catalog_layer,
        tools=tools,
        content_hash=content_hash,
        instance_id=instance_id,
        registered_at=now,
        last_seen_at=now,
        stale=False,
    )
    _upsert_entry(entry)
    revision = _permissions_revision_for(agent, workspace_root)
    return RegisterResult(RegisterStatus.STORED, 200, permissions_revision=revision)


def register_catalog(payload: dict[str, Any]) -> RegisterResult:
    agent = _normalize_agent(payload.get("agent"))
    scope = _normalize_scope(payload.get("scope"))
    if scope is None:
        legacy = str(payload.get("scope") or "").strip().lower()
        if legacy in {"global", "user"}:
            return RegisterResult(
                RegisterStatus.INVALID,
                400,
                "user/global scope registrations are no longer supported; push workspace scope with workspace_root",
            )
        return RegisterResult(RegisterStatus.INVALID, 400, "invalid scope")

    workspace_root = normalize_registry_workspace_path(payload.get("workspace_root"))
    if workspace_root is None:
        return RegisterResult(RegisterStatus.INVALID, 400, "invalid workspace_root")

    catalog_layer = _normalize_catalog_layer(payload.get("catalog_layer"))
    if catalog_layer is None:
        return RegisterResult(RegisterStatus.INVALID, 400, "invalid catalog_layer")

    content_hash = str(payload.get("content_hash") or "").strip()
    instance_id = str(payload.get("instance_id") or "").strip()
    if not content_hash:
        return RegisterResult(RegisterStatus.INVALID, 400, "content_hash required")

    key = _registry_key(agent, workspace_root, catalog_layer)
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

    result = _register_full_tools(
        existing,
        agent=agent,
        scope="workspace",
        workspace_root=workspace_root,
        catalog_layer=catalog_layer,
        tools=tools,
        content_hash=content_hash,
        instance_id=instance_id,
    )
    if result.status in {RegisterStatus.STORED, RegisterStatus.UNCHANGED}:
        from cyt.hook.active_workspace import touch_active_workspace

        touch_active_workspace(agent, workspace_root)
        _hydrate_hook_caches_from_registration(
            tools,
            agent=agent,
            workspace_root=workspace_root,
        )
    return result


def deregister_catalog(payload: dict[str, Any]) -> bool:
    agent = _normalize_agent(payload.get("agent"))
    scope = _normalize_scope(payload.get("scope"))
    if scope is None:
        return False
    workspace_root = normalize_registry_workspace_path(payload.get("workspace_root"))
    if workspace_root is None:
        return False
    catalog_layer = _normalize_catalog_layer(payload.get("catalog_layer"))
    if catalog_layer is None:
        return False
    instance_id = str(payload.get("instance_id") or "").strip() or None
    key = _registry_key(agent, workspace_root, catalog_layer)
    return _remove_entry(key, instance_id=instance_id)


def list_catalog_registrations() -> list[dict[str, Any]]:
    with _registry_lock:
        return [_registration_to_dict(entry) for entry in _registrations.values()]


def _sync_live_registrations_from_daemon() -> int:
    """Merge live hook-daemon registrations into this process (CLI / proxy readers)."""
    from urllib.error import URLError
    from urllib.request import urlopen

    from cyt.hook.daemon_client import resolve_hook_path

    status_url = resolve_hook_path("/hook/catalog/status")
    if not status_url:
        return 0
    try:
        with urlopen(status_url, timeout=1.5) as response:
            payload = json.loads(response.read())
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    registrations = payload.get("registrations")
    if not isinstance(registrations, list):
        return 0

    loaded = 0
    now = time.monotonic()
    for item in registrations:
        if not isinstance(item, dict):
            continue
        entry = _entry_from_dict(item)
        if entry is None:
            continue
        entry.stale = False
        entry.last_seen_at = now
        key = _registry_key(entry.agent, entry.workspace_root or None, entry.catalog_layer)
        with _registry_lock:
            _registrations[key] = entry
        loaded += 1
    return loaded


def hydrate_catalog_registry_for_read() -> int:
    """Load stale disk snapshot, then overlay live hook-daemon registrations."""
    loaded = load_catalog_registry_from_disk(mark_stale=True)
    live = _sync_live_registrations_from_daemon()
    return loaded + live


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


def _union_layer_tools(
    usr_tools: list[dict[str, Any]],
    ws_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union usr + ws catalogs; usr wins on wire name conflict."""
    by_name: dict[str, dict[str, Any]] = {}
    for tool in ws_tools:
        name = str(tool.get("name") or "").strip()
        if name:
            by_name[name] = tool
    for tool in usr_tools:
        name = str(tool.get("name") or "").strip()
        if name:
            by_name[name] = tool
    return list(by_name.values())


def catalog_for_layer(
    agent: str,
    workspace_root: str | Path | None,
    catalog_layer: CatalogLayer,
    *,
    allow_stale: bool = True,
) -> list[dict[str, Any]]:
    """Return one cyt-mcp catalog layer (``usr`` or ``ws``) for runtime hydration."""
    normalized_agent = _normalize_agent(agent)
    ws_path = (
        normalize_registry_workspace_path(str(workspace_root))
        if workspace_root is not None
        else None
    )
    if not ws_path:
        return []
    key = _registry_key(normalized_agent, ws_path, catalog_layer)
    return _entry_tools(_get_entry(key), allow_stale=allow_stale)


def catalog_for_hook(
    agent: str,
    workspace_root: str | Path | None,
    *,
    allow_stale: bool = True,
) -> list[dict[str, Any]]:
    """Return union of usr + ws cyt-mcp catalog layers for hook injection."""
    normalized_agent = _normalize_agent(agent)
    ws_path = (
        normalize_registry_workspace_path(str(workspace_root))
        if workspace_root is not None
        else None
    )
    if not ws_path:
        return []

    usr_key = _registry_key(normalized_agent, ws_path, "usr")
    ws_key = _registry_key(normalized_agent, ws_path, "ws")
    usr_entry = _get_entry(usr_key)
    ws_entry = _get_entry(ws_key)

    usr_tools = _entry_tools(usr_entry, allow_stale=allow_stale)
    ws_tools = _entry_tools(ws_entry, allow_stale=allow_stale)

    if not usr_tools and not ws_tools:
        return []

    if not usr_tools:
        return ws_tools
    if not ws_tools:
        return usr_tools
    return _union_layer_tools(usr_tools, ws_tools)


def merge_catalog_for_hook(
    agent: str,
    workspace_root: str | Path | None,
    *,
    allow_stale: bool = True,
) -> list[dict[str, Any]]:
    """Backward-compatible alias for :func:`catalog_for_hook`."""
    return catalog_for_hook(agent, workspace_root, allow_stale=allow_stale)


def prune_expired_registrations() -> int:
    """Remove entries that exceeded TTL and are not stale fallbacks."""
    global _snapshot_generation
    now = time.monotonic()
    if not _snapshot_idle.wait(timeout=5.0):
        logger.warning(
            "catalog registry snapshot flush timed out waiting for async write",
        )
    with _snapshot_write_lock:
        _snapshot_generation += 1
        generation = _snapshot_generation
    with _snapshot_file_lock:
        with _registry_lock:
            keys_to_remove = [
                key
                for key, entry in _registrations.items()
                if not entry.stale and now - entry.last_seen_at > REGISTRY_TTL_SECONDS
            ]
            removed = len(keys_to_remove)
            if not removed:
                return 0
            for key in keys_to_remove:
                del _registrations[key]
            payload = [_registration_to_dict(entry) for entry in _registrations.values()]
        with _snapshot_write_lock:
            if generation == _snapshot_generation:
                try:
                    _write_registry_snapshot_file(payload)
                except OSError as exc:
                    logger.warning("catalog registry snapshot write failed: %s", exc)
    return removed
