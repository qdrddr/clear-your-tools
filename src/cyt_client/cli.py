"""``cyt-client`` entry point: stdin JSON → POST /hook/inject → stdout injection."""

from __future__ import annotations

import json
import sys

from cyt_client.cursor import format_cursor_stdout, is_cursor_hook_payload
from cyt_client.port import resolve_hook_url
from cyt_client.transcript import enrich_hook_payload
from cyt_client.transport import post_hook_inject


def _write_hook_stdout(body: bytes, *, cursor_output: bool) -> None:
    if cursor_output:
        print(format_cursor_stdout(body.decode()), flush=True)
        return
    if body:
        sys.stdout.buffer.write(body)
        sys.stdout.flush()


def _cursor_output_from_raw(raw: bytes) -> bool:
    if not raw.strip():
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return is_cursor_hook_payload(payload)


def main() -> None:
    raw = sys.stdin.buffer.read()
    cursor_output = _cursor_output_from_raw(raw)
    if not raw.strip():
        if cursor_output:
            print(format_cursor_stdout(""), flush=True)
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

    _write_hook_stdout(body, cursor_output=cursor_output)


if __name__ == "__main__":
    main()
