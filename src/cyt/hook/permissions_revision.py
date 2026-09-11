"""Monotonic permissions revision counter per agent+workspace (hook daemon)."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from cyt.hook.catalog_registry import (
    REGISTRY_SNAPSHOT_DIR,
    _normalize_agent,
    normalize_registry_workspace_path,
)

logger = logging.getLogger(__name__)

PERMISSIONS_REVISION_FILE = REGISTRY_SNAPSHOT_DIR / "permissions_revisions.json"

_revision_lock = threading.Lock()
_revisions: dict[tuple[str, str], int] = {}


def _revision_key(agent: str, workspace_root: str) -> tuple[str, str]:
    return (_normalize_agent(agent), workspace_root)


def _schedule_snapshot_write() -> None:
    thread = threading.Thread(
        target=_write_snapshot,
        name="cyt-permissions-revision-snapshot",
        daemon=True,
    )
    thread.start()


def _write_snapshot() -> None:
    try:
        with _revision_lock:
            payload = {
                f"{agent}:{workspace}": revision
                for (agent, workspace), revision in _revisions.items()
            }
        REGISTRY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = REGISTRY_SNAPSHOT_DIR / f"permissions_revisions.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(PERMISSIONS_REVISION_FILE)
    except OSError as exc:
        logger.warning("permissions revision snapshot write failed: %s", exc)


def load_permissions_revisions_from_disk() -> int:
    """Load revision snapshot; return count loaded."""
    if not PERMISSIONS_REVISION_FILE.is_file():
        return 0
    try:
        raw = json.loads(PERMISSIONS_REVISION_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("permissions revision snapshot read failed: %s", exc)
        return 0
    if not isinstance(raw, dict):
        return 0
    loaded = 0
    with _revision_lock:
        for key, value in raw.items():
            if not isinstance(key, str) or ":" not in key:
                continue
            agent_text, workspace = key.split(":", 1)
            agent = _normalize_agent(agent_text)
            ws = normalize_registry_workspace_path(workspace)
            if ws is None:
                continue
            try:
                revision = int(value)
            except (TypeError, ValueError):
                continue
            _revisions[_revision_key(agent, ws)] = max(0, revision)
            loaded += 1
    logger.info("permissions revisions loaded %d entries from disk", loaded)
    return loaded


def get_permissions_revision(agent: str, workspace_root: str | Path) -> int:
    ws = normalize_registry_workspace_path(str(workspace_root))
    if ws is None:
        return 0
    key = _revision_key(agent, ws)
    with _revision_lock:
        return _revisions.get(key, 0)


def bump_permissions_revision(agent: str, workspace_root: str | Path) -> int:
    ws = normalize_registry_workspace_path(str(workspace_root))
    if ws is None:
        raise ValueError("invalid workspace_root")
    key = _revision_key(agent, ws)
    with _revision_lock:
        current = _revisions.get(key, 0)
        next_revision = current + 1
        _revisions[key] = next_revision
    _schedule_snapshot_write()
    return next_revision


def clear_permissions_revisions() -> None:
    with _revision_lock:
        _revisions.clear()
