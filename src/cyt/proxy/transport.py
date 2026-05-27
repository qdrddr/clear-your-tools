"""Shared HTTP forwarding utilities for the reverse proxy."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import uvicorn
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp

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


def _rotate_debug_log(path: Path) -> None:
    backup = path.with_name(f"{path.name}.1")
    backup.unlink(missing_ok=True)
    path.replace(backup)


def append_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> None:
    timestamp = datetime.now(UTC).isoformat()
    block = f"--- {timestamp} ---\n{json.dumps(snapshot, indent=2)}\n"
    if max_bytes is not None and max_bytes > 0 and path.exists():
        block_size = len(block.encode("utf-8"))
        if path.stat().st_size + block_size > max_bytes:
            _rotate_debug_log(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


async def save_debug_snapshot(
    path: Path,
    snapshot: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> Path:
    await asyncio.to_thread(append_debug_snapshot, path, snapshot, max_bytes=max_bytes)
    return path


def reverse_debug_log_path(endpoint_name: str) -> Path:
    return Path(f"{endpoint_name}.log")


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
            async for chunk in upstream_resp.aiter_bytes():
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
    await server.serve()
