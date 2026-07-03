"""``cyt-client`` entry point: stdin JSON → POST /hook/inject → stdout injection."""

from __future__ import annotations

import subprocess
import sys

from cyt_client.port import resolve_hook_url
from cyt_client.transcript import enrich_hook_payload
from cyt_client.transport import parse_error_body, post_hook_inject


def _fallback_stdin_hook(payload_bytes: bytes) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "cyt.skills.cli", "--stdin"],
        input=payload_bytes,
        capture_output=True,
        check=False,
    )
    if proc.stdout:
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.flush()
    if proc.returncode != 0 and proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
    return proc.returncode


def main() -> None:
    payload_bytes = enrich_hook_payload(sys.stdin.buffer.read())
    if not payload_bytes.strip():
        return

    hook_url = resolve_hook_url()
    if hook_url is None:
        print(
            "cyt-client: hook server unavailable; falling back to local hook handler",
            file=sys.stderr,
        )
        raise SystemExit(_fallback_stdin_hook(payload_bytes))

    try:
        status, body = post_hook_inject(hook_url, payload_bytes)
    except ConnectionError as exc:
        print(f"cyt-client: {exc}; falling back to local hook handler", file=sys.stderr)
        raise SystemExit(_fallback_stdin_hook(payload_bytes)) from exc

    if status >= 400:
        print(
            f"cyt-client: {parse_error_body(body)}; falling back to local hook handler",
            file=sys.stderr,
        )
        raise SystemExit(_fallback_stdin_hook(payload_bytes))

    if body:
        sys.stdout.buffer.write(body)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
