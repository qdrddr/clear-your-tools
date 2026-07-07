"""``cyt-client`` entry point: stdin JSON → POST /hook/inject → stdout injection."""

from __future__ import annotations

import json
import sys

from cyt_client.cursor import (
    format_cursor_continue,
    format_cursor_stdout,
    is_cursor_hook_payload,
    is_cursor_rules_cleanup_event,
)
from cyt_client.port import resolve_hook_url
from cyt_client.rules_file import (
    delete_cursor_rules_file,
    extract_additional_context,
    sync_cursor_rules_file,
    workspace_root_from_payload,
)
from cyt_client.transcript import enrich_hook_payload
from cyt_client.transport import post_hook_inject


def _parse_payload(raw: bytes) -> dict | None:
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_hook_stdout(body: bytes, *, cursor_output: bool) -> None:
    if cursor_output:
        print(format_cursor_stdout(body.decode()), flush=True)
        return
    if body:
        sys.stdout.buffer.write(body)
        sys.stdout.flush()


def _handle_cursor_rules_cleanup(payload: dict) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is not None:
        delete_cursor_rules_file(workspace)
    print(format_cursor_continue(), flush=True)


def _handle_cursor_before_submit(raw: bytes, payload: dict) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        print(format_cursor_continue(), flush=True)
        return

    payload_bytes = enrich_hook_payload(raw)
    hook_url = resolve_hook_url()
    if hook_url is None:
        print(format_cursor_continue(), flush=True)
        return

    try:
        status, body = post_hook_inject(hook_url, payload_bytes)
    except ConnectionError:
        print(format_cursor_continue(), flush=True)
        return

    if status >= 400:
        print(format_cursor_continue(), flush=True)
        return

    sync_cursor_rules_file(workspace, extract_additional_context(body))
    print(format_cursor_stdout(body.decode()), flush=True)


def main() -> None:
    raw = sys.stdin.buffer.read()
    payload = _parse_payload(raw)
    cursor_output = payload is not None and is_cursor_hook_payload(payload)

    if payload is None:
        if cursor_output:
            print(format_cursor_continue(), flush=True)
        return

    if cursor_output and is_cursor_rules_cleanup_event(payload):
        _handle_cursor_rules_cleanup(payload)
        return

    if cursor_output:
        _handle_cursor_before_submit(raw, payload)
        return

    if not raw.strip():
        return

    payload_bytes = enrich_hook_payload(raw)
    hook_url = resolve_hook_url()
    if hook_url is None:
        return

    try:
        status, body = post_hook_inject(hook_url, payload_bytes)
    except ConnectionError:
        return

    if status >= 400:
        return

    _write_hook_stdout(body, cursor_output=False)


if __name__ == "__main__":
    main()
