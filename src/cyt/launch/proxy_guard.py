"""Ensure a local CYT reverse proxy is running before agent launch."""

from __future__ import annotations

import atexit
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_HOST = "127.0.0.1"
HEALTH_TIMEOUT_SECONDS = 0.5
STARTUP_POLL_SECONDS = 0.2
STARTUP_TIMEOUT_SECONDS = 15.0


@dataclass
class ProxyGuard:
    """Tracks a background proxy process started by launch."""

    process: subprocess.Popen[bytes] | None
    started_by_launch: bool

    def terminate_if_started(self) -> None:
        if not self.started_by_launch or self.process is None:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _health_ok(port: int) -> bool:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            if not isinstance(code, int) or code != 200:
                return False
            payload = json.loads(response.read())
            return isinstance(payload, dict) and payload.get("status") == "ok"
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def _spawn_proxy(
    *,
    port: int,
    config_path: Path | None,
    quiet: bool,
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "cyt.proxy.cli",
        "proxy",
        "--port",
        str(port),
        "--quiet",
    ]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_proxy(
    *,
    port: int,
    config_path: Path | None = None,
    quiet: bool = False,
) -> ProxyGuard:
    """Return immediately when proxy is healthy; otherwise spawn one in the background."""
    if _health_ok(port):
        return ProxyGuard(process=None, started_by_launch=False)

    process = _spawn_proxy(port=port, config_path=config_path, quiet=quiet)
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"cyt proxy exited before becoming healthy on port {port}.",
            )
        if _health_ok(port):
            guard = ProxyGuard(process=process, started_by_launch=True)
            atexit.register(guard.terminate_if_started)
            return guard
        time.sleep(STARTUP_POLL_SECONDS)

    process.terminate()
    raise SystemExit(
        f"Timed out waiting for cyt proxy on http://{LOCAL_HOST}:{port}/health",
    )
