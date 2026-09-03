"""Shared HTTP forwarding utilities for the reverse proxy."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Coroutine
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import uvicorn
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp

from cyt.platform.filelock import exclusive_file_lock

INTERRUPTED_EXIT_CODE = 130

debug_endpoint_proxy_log_path: ContextVar[Path | None] = ContextVar(
    "debug_endpoint_proxy_log_path",
    default=None,
)

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    },
)


def http2_package_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("h2") is not None


def filter_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def header_content_encoding(headers: dict[str, str]) -> str | None:
    return headers.get("content-encoding") or headers.get("connect-content-encoding")


def append_debug_log_block(path: Path, *, label: str, content: str) -> None:
    """Append a labeled block to the endpoint debug log (never rotates/truncates)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()
    body = content if content.endswith("\n") else f"{content}\n"
    block = f"--- {timestamp} {label} ---\n{body}"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def append_debug_json_entry(path: Path, entry: dict[str, Any]) -> None:
    """Append a request object to a JSON array debug file (never rotates/truncates)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with (
        lock_path.open("a", encoding="utf-8") as lock_f,
        exclusive_file_lock(
            lock_f.fileno(),
        ),
    ):
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if content.strip():
            entries = json.loads(content)
            if not isinstance(entries, list):
                raise ValueError(f"debug JSON file must contain an array: {path}")
        else:
            entries = []
        entries.append(entry)
        encoded = json.dumps(entries, indent=2, default=str)
        if not encoded.endswith("\n"):
            encoded += "\n"
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(encoded, encoding="utf-8")
        os.replace(tmp_path, path)


def append_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> None:
    del max_bytes  # body truncation is applied when building the snapshot
    append_debug_json_entry(path, snapshot)


async def save_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> Path:
    await asyncio.to_thread(append_debug_snapshot, path, snapshot, max_bytes=max_bytes)
    return path


def new_debug_session_id() -> str:
    if env_value := os.environ.get("CYT_DEBUG_SESSION_ID"):
        return env_value
    return uuid4().hex[:12]


def reverse_debug_log_path(
    endpoint_name: str,
    *,
    debug_log_dir: Path | None = None,
) -> Path:
    name = f"{endpoint_name}.json"
    if debug_log_dir is not None:
        return debug_log_dir / name
    return Path(name)


def reverse_debug_original_log_path(
    endpoint_name: str,
    *,
    debug_log_dir: Path | None = None,
) -> Path:
    """Pre-mutation request JSON log for diffing against ``reverse_debug_log_path``."""
    name = f"{endpoint_name}-original.json"
    if debug_log_dir is not None:
        return debug_log_dir / name
    return Path(name)


def reverse_debug_proxy_log_path(
    endpoint_name: str,
    *,
    debug_log_dir: Path | None = None,
) -> Path:
    """Pruning/operator debug log (separate from JSON request snapshots in ``reverse_debug_log_path``)."""
    name = f"{endpoint_name}-proxy.log"
    if debug_log_dir is not None:
        return debug_log_dir / name
    return Path(name)


def append_original_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> None:
    del max_bytes
    append_debug_json_entry(path, snapshot)


async def save_original_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> Path:
    await asyncio.to_thread(
        append_original_debug_snapshot,
        path,
        snapshot,
        max_bytes=max_bytes,
    )
    return path


def agent_trace_log_path(debug_log_dir: Path, session_id: str) -> Path:
    return debug_log_dir / f"trace-{session_id}.log"


def append_agent_trace_log(
    path: Path,
    *,
    session_id: str,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessionId": session_id,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass


async def iter_request_body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        yield chunk


async def forward_upstream(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    stream_request: bool,
    request: Request | None = None,
) -> Response:
    if stream_request:
        if request is None:
            raise ValueError("request is required when stream_request=True")
        content: bytes | AsyncIterator[bytes] | None = iter_request_body(request)
    else:
        content = body if body else None

    upstream_req = client.build_request(
        method,
        url,
        headers=headers,
        content=content,
    )
    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        return Response(f"Upstream error: {exc}", status_code=502)

    async def response_stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        response_stream(),
        status_code=upstream_resp.status_code,
        headers=filter_headers(upstream_resp.headers),
    )


async def run_hypercorn_async(
    app: ASGIApp,
    *,
    host: str,
    port: int,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    try:
        from hypercorn.asyncio import serve
        from hypercorn.config import Config as HypercornConfig
        from hypercorn.typing import ASGIFramework
    except ImportError as exc:
        print(
            "HTTP/2 server requires hypercorn with h2 support: pip install 'hypercorn[h2]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if not http2_package_available():
        print("HTTP/2 server requires the h2 package: pip install h2", file=sys.stderr)
        raise SystemExit(1)

    cfg = HypercornConfig()
    cfg.bind = [f"{host}:{port}"]
    if ssl_keyfile and ssl_certfile:
        cfg.keyfile = ssl_keyfile
        cfg.certfile = ssl_certfile
        cfg.alpn_protocols = ["h2", "http/1.1"]
    else:
        print(
            "HTTP/2 (serve) requires TLS; set network.proxy.reverse.http2.ssl or pass --ssl-keyfile",
            file=sys.stderr,
        )
        raise SystemExit(1)

    await serve(cast(ASGIFramework, app), cfg)


def run_async_cli(coro: Coroutine[Any, Any, None]) -> None:
    """Run an async CLI coroutine; exit 130 on Ctrl+C without a traceback."""
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None


async def run_uvicorn_async(
    app: ASGIApp,
    *,
    host: str,
    port: int,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        log_level="info",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
