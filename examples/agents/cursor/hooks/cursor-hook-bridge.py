#!/usr/bin/env python3
"""Adapt Cursor hook stdin/stdout for cyt hook injection."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

_CURSOR_TO_CYT_EVENT = {
    "beforeSubmitPrompt": "UserPromptSubmit",
    "sessionStart": "SessionStart",
}


def _adapt_input(data: dict[str, Any]) -> dict[str, Any]:
    event = data.get("hook_event_name") or data.get("hookEventName") or ""
    if not isinstance(event, str):
        event = ""
    adapted = dict(data)
    cyt_event = _CURSOR_TO_CYT_EVENT.get(event.strip(), event.strip())
    if cyt_event:
        adapted["hook_event_name"] = cyt_event

    if not adapted.get("cwd"):
        roots = adapted.get("workspace_roots")
        if isinstance(roots, list) and roots:
            first = roots[0]
            if isinstance(first, str) and first.strip():
                adapted["cwd"] = first.strip()

    if not adapted.get("session_id"):
        conversation_id = adapted.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            adapted["session_id"] = conversation_id.strip()

    return adapted


def _cursor_output(cyt_stdout: str) -> str:
    if not cyt_stdout.strip():
        return json.dumps({"continue": True})

    try:
        data = json.loads(cyt_stdout)
    except json.JSONDecodeError:
        return json.dumps({"continue": True})

    if not isinstance(data, dict):
        return json.dumps({"continue": True})

    hook_output = data.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return json.dumps({"continue": True})

    context = hook_output.get("additionalContext") or hook_output.get("additional_context")
    if isinstance(context, str) and context.strip():
        return json.dumps({"continue": True, "additional_context": context})

    return json.dumps({"continue": True})


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"continue": True}))
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"continue": True}))
        return 0

    if not isinstance(payload, dict):
        print(json.dumps({"continue": True}))
        return 0

    adapted = _adapt_input(payload)
    proc = subprocess.run(
        [sys.executable, "-m", "cyt_client.cli"],
        input=json.dumps(adapted).encode(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 and proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
    print(_cursor_output(proc.stdout.decode()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
