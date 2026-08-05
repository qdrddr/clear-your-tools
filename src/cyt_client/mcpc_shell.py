"""Parse mcpc Shell CLI invocations for PreToolUse validation."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any, cast

_MCPC_TOOLS_CALL_RE = re.compile(
    r"mcpc\s+(@[\w.-]+)\s+tools-call\s+([^\s|;&]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class McpcShellCall:
    session: str
    tool_name: str
    args: dict[str, Any]


def parse_mcpc_shell_command(command: str) -> McpcShellCall | None:
    """Extract mcpc @session tools-call payload from a Shell command string."""
    text = str(command or "").strip()
    if not text or "mcpc" not in text.lower():
        return None

    match = _MCPC_TOOLS_CALL_RE.search(text)
    if match is None:
        return None

    session = match.group(1).strip()
    tool_name = match.group(2).strip()
    if not session or not tool_name:
        return None

    args = _extract_json_payload(text)
    if args is None:
        args = {}
    return McpcShellCall(session=session, tool_name=tool_name, args=args)


def _extract_json_payload(command: str) -> dict[str, Any] | None:
    echo_match = re.search(r"echo\s+(.+?)\s*\|\s*mcpc\s+", command, re.IGNORECASE | re.DOTALL)
    if echo_match:
        raw = echo_match.group(1).strip()
        parsed = _parse_shell_json_literal(raw)
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)

    after_call = command.split("tools-call", 1)
    if len(after_call) == 2:
        tail = after_call[1].strip()
        parts = tail.split(None, 1)
        if len(parts) == 2:
            parsed = _parse_shell_json_literal(parts[1].strip())
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)
    return None


def _parse_shell_json_literal(raw: str) -> object | None:
    stripped = raw.strip()
    if not stripped:
        return None
    # unwrap single quotes from echo '...'
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        stripped = stripped[1:-1]
    try:
        return cast(object, json.loads(stripped))
    except json.JSONDecodeError:
        try:
            tokens = shlex.split(stripped)
            if tokens:
                return cast(object, json.loads(tokens[0]))
        except (json.JSONDecodeError, ValueError):
            return None
    return None
