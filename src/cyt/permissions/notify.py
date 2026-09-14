"""Notify hook daemon when permissions overlays change."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt.hook.daemon_client import resolve_hook_path

PERMISSIONS_CHANGED_PATH = "/hook/permissions/changed"
NOTIFY_TIMEOUT_SECONDS = 0.5


def resolve_permissions_changed_url() -> str | None:
    return resolve_hook_path(PERMISSIONS_CHANGED_PATH)


def notify_permissions_changed(
    *,
    workspace_root: Path | str,
    agent: str = "cursor",
) -> bool:
    """POST permissions-changed to hook daemon. Returns True when daemon acknowledged."""
    url = resolve_permissions_changed_url()
    if url is None:
        return False
    try:
        resolved = Path(workspace_root).expanduser().resolve()
    except OSError:
        return False
    if not resolved.is_dir():
        return False
    body = {
        "workspace_root": str(resolved),
        "agent": str(agent or "cursor").strip().lower() or "cursor",
    }
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=NOTIFY_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            return isinstance(code, int) and 200 <= code < 300
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def print_permissions_reload_notice(*, notified: bool) -> None:
    if notified:
        print("Notified hook daemon; cyt-mcp will reload permissions on next push.")
    else:
        print(
            "Restart the agent or refresh cyt-mcp for MCP catalog changes to apply "
            "(hook daemon not reachable).",
        )
