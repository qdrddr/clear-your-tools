"""NDJSON debug logger for agent-mode hook/prune investigations."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any

_SESSION_ID = "01de34"

def _log_path() -> Path:
    explicit = os.environ.get("CYT_AGENT_DEBUG_LOG")
    if explicit:
        return Path(explicit)
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").is_dir():
            return parent / "debug-01de34.log"
    return Path(__file__).resolve().parents[3] / "debug-01de34.log"

def agent_debug_log(location: str, message: str, data: dict[str, Any] | None = None, *, hypothesis_id: str = "", run_id: str = "pre-fix") -> None:
    try:
        payload = {"sessionId": _SESSION_ID, "timestamp": int(time.time() * 1000), "location": location, "message": message, "data": data or {}, "hypothesisId": hypothesis_id, "runId": run_id}
        path = _log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
