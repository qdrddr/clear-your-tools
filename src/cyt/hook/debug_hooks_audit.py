"""Debug-session audit logging for hooks.json writes (session ee2f62)."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

_DEBUG_LOG_PATH = Path(
    "/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/qdrddr/clear-your-tools/.cursor/debug-ee2f62.log",
)
_SESSION_ID = "ee2f62"


def _is_hooks_json(path: Path) -> bool:
    return path.name == "hooks.json"


def log_hooks_json_mutation(
    path: Path,
    *,
    action: str,
    hypothesis_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Append one NDJSON audit record when a hooks.json file is mutated."""
    if not _is_hooks_json(path):
        return
    payload: dict[str, Any] = {
        "sessionId": _SESSION_ID,
        "timestamp": int(time.time() * 1000),
        "location": "cyt.hook.debug_hooks_audit",
        "message": action,
        "hypothesisId": hypothesis_id,
        "data": {
            "path": str(path),
            "stack": traceback.format_stack(limit=8)[:-1],
            **(data or {}),
        },
    }
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
