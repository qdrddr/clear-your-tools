"""Transparent HTTP reverse proxy for LLM API endpoints."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any
import httpx
import uvicorn
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

DEFAULT_PORT = 8000
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
    }
)
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

logger = logging.getLogger(__name__)


def load_proxy_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).with_name("proxy.yaml")
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid proxy config in {config_path}")
    return data


def build_routes(proxy_cfg: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    upstreams = {
        item["upstream"]: item for item in proxy_cfg.get("upstreams", [])
    }
    routes: dict[str, tuple[str, str | None]] = {}
    for endpoint in proxy_cfg.get("endpoints", []):
        if endpoint not in upstreams:
            raise ValueError(f"No upstream configured for endpoint: {endpoint}")
        entry = upstreams[endpoint]
        kind = entry.get("kind")
        routes[f"/{endpoint}"] = (entry["url"].rstrip("/"), kind)
    if not routes:
        raise ValueError("No proxy endpoints configured")
    return routes


def resolve_upstream(
    path: str,
    routes: dict[str, tuple[str, str | None]],
) -> tuple[str, str, str | None] | None:
    for prefix in sorted(routes, key=len, reverse=True):
        if path == prefix or path.startswith(prefix + "/"):
            suffix = path[len(prefix) :] if path != prefix else ""
            upstream_base, kind = routes[prefix]
            return upstream_base, suffix, kind
    return None


def filter_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP
    }


async def transform_request_body(
    body: bytes,
    content_type: str | None,
    kind: str | None,
) -> bytes:
    if kind != "anthropic" or not body:
        return body
    if not content_type or "json" not in content_type.lower():
        return body
    try:
        from proxy_anthropic import transform_anthropic_request

        payload = json.loads(body)
        transformed = await asyncio.to_thread(transform_anthropic_request, payload)
        return json.dumps(transformed).encode()
    except json.JSONDecodeError:
        return body
    except Exception as exc:
        logger.warning("anthropic transform failed: %s", exc)
        return body


def create_app(routes: dict[str, tuple[str, str | None]]) -> Starlette:
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def proxy(request: Request) -> Response:
        match = resolve_upstream(request.url.path, routes)
        if match is None:
            return Response("Not Found", status_code=404)

        upstream_base, path_suffix, kind = match
        query = request.url.query
        target_url = f"{upstream_base}{path_suffix}"
        if query:
            target_url = f"{target_url}?{query}"

        body = await request.body()
        body = await transform_request_body(
            body,
            request.headers.get("content-type"),
            kind,
        )
        forward_headers = filter_headers(request.headers)

        client = httpx.AsyncClient(timeout=None)
        try:
            upstream_req = client.build_request(
                request.method,
                target_url,
                headers=forward_headers,
                content=body if body else None,
            )
            upstream_resp = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            return Response(f"Upstream error: {exc}", status_code=502)

        async def stream() -> Any:
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(),
            status_code=upstream_resp.status_code,
            headers=filter_headers(upstream_resp.headers),
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", proxy, methods=METHODS),
        ]
    )


def resolve_port(config: dict[str, Any], cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    proxy_cfg = config.get("network", {}).get("proxy", {})
    return int(proxy_cfg.get("port", DEFAULT_PORT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Transparent LLM HTTP proxy")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (overrides proxy.yaml; default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to proxy.yaml",
    )
    args = parser.parse_args()

    config = load_proxy_config(args.config)
    proxy_cfg = config["network"]["proxy"]
    routes = build_routes(proxy_cfg)
    port = resolve_port(config, args.port)
    app = create_app(routes)

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
