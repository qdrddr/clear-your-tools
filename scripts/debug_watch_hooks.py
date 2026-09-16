#!/usr/bin/env python3
"""Poll hooks.json and log content/mtime changes for debug session ee2f62."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

LOG_PATH = Path(
    "/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/qdrddr/clear-your-tools/.cursor/debug-ee2f62.log",
)
WATCH_PATHS = (
    Path("~/.cursor/hooks.json").expanduser(),
    Path(
        "/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/qdrddr/clear-your-tools/.cursor/hooks.json",
    ),
)


def _snapshot(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"exists": False}
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    event_count = 0
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("hooks"), dict):
            event_count = len(payload["hooks"])
    except json.JSONDecodeError:
        pass
    stat = path.stat()
    return {
        "exists": True,
        "sha256_prefix": digest,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "event_count": event_count,
    }


def _log(message: str, data: dict[str, object]) -> None:
    payload = {
        "sessionId": "ee2f62",
        "timestamp": int(time.time() * 1000),
        "location": "scripts/debug_watch_hooks.py",
        "message": message,
        "hypothesisId": "A",
        "data": data,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def main() -> None:
    previous = {str(path): _snapshot(path) for path in WATCH_PATHS}
    _log("watch_start", {"paths": previous})
    print("Watching hooks.json files (Ctrl+C to stop)...", flush=True)
    while True:
        time.sleep(2)
        for path in WATCH_PATHS:
            current = _snapshot(path)
            key = str(path)
            if current != previous[key]:
                _log(
                    "hooks_file_changed",
                    {"path": key, "before": previous[key], "after": current},
                )
                print(f"CHANGE {path}: {previous[key]} -> {current}", flush=True)
                previous[key] = current


if __name__ == "__main__":
    main()
