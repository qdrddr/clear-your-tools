"""Smoke test: released ``cyt-client`` Cursor hook stdin → POST → stdout."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_INJECTION = "<agent-skills><skill name='executor'># execute</skill></agent-skills>"
_INJECT_BODY = json.dumps(
    {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _INJECTION,
        },
    },
).encode()


class _HookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"name":"cyt","status":"ok","hook":true}')
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(_INJECT_BODY)

    def log_message(self, *_args: object) -> None:
        return


def _run_cyt_client(payload: dict[str, object], *, hook_url: str | None) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    if hook_url is not None:
        env["CYT_HOOK_URL"] = hook_url
    else:
        env.pop("CYT_HOOK_URL", None)
    return subprocess.run(
        ["cyt-client"],
        input=json.dumps(payload).encode(),
        env=env,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_cyt_client_cursor_before_submit_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        payload = {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "make a small hook test",
            "conversation_id": "3027dc36-e934-4d72-9e2d-1344934a49bb",
            "cursor_version": "3.11.13",
            "workspace_roots": [str(workspace)],
        }
        server = HTTPServer(("127.0.0.1", 0), _HookHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_cyt_client(
                payload,
                hook_url=f"http://127.0.0.1:{port}/hook/inject",
            )
        finally:
            server.shutdown()

        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == {
            "continue": True,
            "additional_context": _INJECTION,
        }
        rules = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        assert rules.is_file()
        assert _INJECTION in rules.read_text(encoding="utf-8")


def test_cyt_client_cursor_before_submit_empty_injection() -> None:
    class _EmptyHandler(_HookHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b'{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}',
            )

    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "make a small hook test",
            "cursor_version": "3.11.13",
            "workspace_roots": [str(Path(tmp))],
        }
        server = HTTPServer(("127.0.0.1", 0), _EmptyHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_cyt_client(
                payload,
                hook_url=f"http://127.0.0.1:{port}/hook/inject",
            )
        finally:
            server.shutdown()

        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == {"continue": True}
