"""Transparent HTTP reverse proxy for LLM API endpoints."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp


"""
# run in http/2 mode (requires h2 package and TLS)
uv pip install h2
uv pip install 'hypercorn[h2]'
openssl req -x509 -nodes -days 365 -newkey rsa:4096 -keyout src/crt/key.pem -out src/crt/cert.pem -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

uv run src/proxy.py serve --http2-serve \
        --ssl-keyfile src/crt/key.pem \
        --ssl-certfile src/crt/cert.pem
"""
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
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

logger = logging.getLogger(__name__)


def _http2_package_available() -> bool:
    try:
        import h2  # noqa: F401
    except ImportError:
        return False
    return True


def _needs_request_buffer(
    *,
    kind: str | None,
    method: str,
    debug: bool,
) -> bool:
    """Anthropic pruning and debug snapshots require a full request body."""
    if debug:
        return True
    if kind != "anthropic":
        return False
    return method.upper() in BODY_METHODS


async def _iter_request_body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        yield chunk


async def _forward_upstream(
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
        content: bytes | AsyncIterator[bytes] = _iter_request_body(request)
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


def _proxy_http2_settings(config: dict[str, Any]) -> dict[str, Any]:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    http2_cfg = proxy_cfg.get("http2")
    if isinstance(http2_cfg, bool):
        return {
            "http2_upstream": http2_cfg,
            "http2_serve": False,
            "ssl_keyfile": None,
            "ssl_certfile": None,
        }
    if not isinstance(http2_cfg, dict):
        http2_cfg = {}
    ssl_cfg = http2_cfg.get("ssl") if isinstance(http2_cfg.get("ssl"), dict) else {}
    return {
        "http2_upstream": bool(http2_cfg.get("upstream", False)),
        "http2_serve": bool(http2_cfg.get("serve", False)),
        "ssl_keyfile": ssl_cfg.get("keyfile") or http2_cfg.get("ssl_keyfile"),
        "ssl_certfile": ssl_cfg.get("certfile") or http2_cfg.get("ssl_certfile"),
    }


def _run_hypercorn(
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
    except ImportError as exc:
        print(
            "HTTP/2 server requires hypercorn with h2 support: "
            "pip install 'hypercorn[h2]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if not _http2_package_available():
        print(
            "HTTP/2 server requires the h2 package: pip install h2",
            file=sys.stderr,
        )
        raise SystemExit(1)

    cfg = HypercornConfig()
    cfg.bind = [f"{host}:{port}"]
    if ssl_keyfile and ssl_certfile:
        cfg.keyfile = ssl_keyfile
        cfg.certfile = ssl_certfile
        cfg.alpn_protocols = ["h2", "http/1.1"]
    else:
        print(
            "HTTP/2 (serve) requires TLS; set network.proxy.http2.ssl keyfile/certfile "
            "or pass --ssl-keyfile / --ssl-certfile",
            file=sys.stderr,
        )
        raise SystemExit(1)

    asyncio.run(serve(app, cfg))


def run_proxy_server(
    app: Starlette,
    *,
    host: str,
    port: int,
    http2_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    if http2_serve:
        _run_hypercorn(
            app,
            host=host,
            port=port,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        )
        return
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


def load_proxy_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).with_name("config.yaml")
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid proxy config in {config_path}")
    return data


def _reverse_proxy_cfg(proxy_cfg: dict[str, Any]) -> dict[str, Any]:
    reverse = proxy_cfg.get("reverse")
    if isinstance(reverse, dict):
        return reverse
    if proxy_cfg.get("upstreams") or proxy_cfg.get("endpoints"):
        return proxy_cfg
    raise ValueError("network.proxy.reverse must be configured")


def build_routes(proxy_cfg: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    reverse_cfg = _reverse_proxy_cfg(proxy_cfg)
    upstreams = {
        item["upstream"]: item for item in reverse_cfg.get("upstreams", [])
    }
    routes: dict[str, tuple[str, str | None]] = {}
    for endpoint in reverse_cfg.get("endpoints", []):
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
) -> tuple[str, str, str | None, str] | None:
    for prefix in sorted(routes, key=len, reverse=True):
        if path == prefix or path.startswith(prefix + "/"):
            suffix = path[len(prefix) :] if path != prefix else ""
            upstream_base, kind = routes[prefix]
            endpoint_name = prefix.lstrip("/")
            return upstream_base, suffix, kind, endpoint_name
    return None


def _debug_log_path(endpoint_name: str) -> Path:
    return Path(f"{endpoint_name}.log")


def _body_for_snapshot(body: bytes, content_type: str | None) -> Any:
    if not body:
        return None
    if content_type and "json" in content_type.lower():
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            pass
    return {"_base64": base64.b64encode(body).decode("ascii")}


def _append_debug_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    timestamp = datetime.now(UTC).isoformat()
    block = f"--- {timestamp} ---\n{json.dumps(snapshot, indent=2)}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


async def _save_debug_snapshot(
    endpoint_name: str,
    snapshot: dict[str, Any],
) -> Path:
    path = _debug_log_path(endpoint_name)
    await asyncio.to_thread(_append_debug_snapshot, path, snapshot)
    return path


def filter_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP
    }


def _pruning_pipeline_from_config(config: dict[str, Any]) -> list[str]:
    pruning = config.get("pruning")
    pipeline = pruning.get("pipeline") if isinstance(pruning, dict) else None
    if pipeline is None:
        return ["rerank"]
    if not isinstance(pipeline, list) or not all(isinstance(s, str) for s in pipeline):
        raise ValueError("pruning.pipeline must be a list of stage names")
    return pipeline


def _catalog_file_paths(catalog: dict[str, Any], key: str) -> list[str]:
    items = catalog.get(key)
    if not isinstance(items, list):
        return []
    paths: list[str] = []
    for item in items:
        if isinstance(item, dict):
            path = item.get("file_path")
            if path:
                paths.append(str(path))
    return sorted(paths)


def _format_decomposed_table_lines(pruning: dict[str, Any]) -> list[str]:
    """Table of json vs enum (md) counts per pipeline stage."""
    breakdown = pruning.get("decomposed_breakdown") or {}
    decomposed = pruning.get("decomposed") or {}
    stage_order = ("build_index", "rerank", "llm")
    stages = [s for s in stage_order if s in breakdown or s in decomposed]
    if not stages:
        return ["Decomposed items: (none)"]

    rows: list[tuple[str, str, str]] = []
    for stage in stages:
        if stage in breakdown:
            counts = breakdown[stage]
            json_n = str(counts.get("json", 0))
            md_n = str(counts.get("md", 0))
        else:
            total = decomposed.get(stage, "-")
            json_n = str(total)
            md_n = "-"
        rows.append((stage, json_n, md_n))

    col_stage = max(len("stage"), max(len(r[0]) for r in rows))
    col_json = max(len("json"), max(len(r[1]) for r in rows))
    col_md = max(len("enum (md)"), max(len(r[2]) for r in rows))
    header = f"{'stage':<{col_stage}}  {'json':>{col_json}}  {'enum (md)':>{col_md}}"
    sep = f"{'-' * col_stage}  {'-' * col_json}  {'-' * col_md}"
    body = [f"{stage:<{col_stage}}  {json_n:>{col_json}}  {md_n:>{col_md}}" for stage, json_n, md_n in rows]
    return ["Decomposed items:", header, sep, *body]


def _format_decomposed_paths_lines(pruning: dict[str, Any]) -> list[str]:
    """List file_path values per stage, json and enum (md) printed separately."""
    catalog_by_stage = pruning.get("decomposed_catalog")
    if not isinstance(catalog_by_stage, dict) or not catalog_by_stage:
        return []

    lines: list[str] = [""]
    stage_order = ("build_index", "rerank", "llm")
    for stage in stage_order:
        catalog = catalog_by_stage.get(stage)
        if not isinstance(catalog, dict):
            continue
        json_paths = _catalog_file_paths(catalog, "json")
        md_paths = _catalog_file_paths(catalog, "md")
        lines.append(f"{stage} json ({len(json_paths)}):")
        lines.extend(f"  {p}" for p in json_paths)
        lines.append(f"{stage} enum (md) ({len(md_paths)}):")
        lines.extend(f"  {p}" for p in md_paths)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _print_debug_pruning(pruning: dict[str, Any] | None) -> None:
    """Print pruning summary to the terminal (uvicorn hides app logger.info by default)."""
    lines: list[str] = [""]
    if pruning is None:
        lines.extend(["user query:", "(none — body was not transformed)", ""])
    else:
        query = pruning.get("query")
        lines.extend(["user query:", ""])
        lines.append(query if query else "(none extracted)")
        lines.append("")
        lines.extend(_format_decomposed_table_lines(pruning))
        lines.extend(_format_decomposed_paths_lines(pruning))
        status = pruning.get("status")
        if status:
            tools_in = pruning.get("tools_in")
            tools_out = pruning.get("tools_out")
            lines.append(f"pruning status: {status} (tools {tools_in} -> {tools_out})")
        tokens_in = pruning.get("tokens_in")
        if tokens_in is not None:
            tokens_out = pruning.get("tokens_out")
            tokens_saved = pruning.get("tokens_saved")
            if tokens_out is not None and tokens_saved is not None:
                pct = (100.0 * tokens_saved / tokens_in) if tokens_in else 0.0
                lines.append(
                    f"tool tokens (compact JSON): {tokens_in} -> {tokens_out} "
                    f"(saved {tokens_saved}, {pct:.1f}%)",
                )
            else:
                lines.append(f"tool tokens (compact JSON): input={tokens_in}")
        pruning_model_tokens = pruning.get("pruning_model_tokens") or {}
        if pruning_model_tokens:
            parts = ", ".join(
                f"{stage}={pruning_model_tokens[stage]}"
                for stage in ("rerank", "llm")
                if stage in pruning_model_tokens
            )
            lines.append(f"pruning model tokens: {parts}")
        error = pruning.get("error")
        if error:
            lines.append(f"pruning error: {error}")
    lines.append("")
    print("\n".join(lines), flush=True)


async def transform_request_body(
    body: bytes,
    content_type: str | None,
    kind: str | None,
    pruning_pipeline: list[str] | None = None,
    debug: bool = False,
) -> tuple[bytes, Any | None]:
    if kind != "anthropic" or not body:
        return body, None
    if not content_type or "json" not in content_type.lower():
        return body, None
    try:
        from proxy_anthropic import PruneResult, transform_anthropic_request

        payload = json.loads(body)
        transformed, pruning = await asyncio.to_thread(
            transform_anthropic_request,
            payload,
            pruning_pipeline,
            capture_decomposed_catalog=debug,
        )
        return json.dumps(transformed).encode(), pruning
    except json.JSONDecodeError:
        return body, None
    except Exception as exc:
        logger.warning("anthropic transform failed: %s", exc)
        from proxy_anthropic import PruneResult

        return body, PruneResult(
            tools=None,
            status="failed",
            query=None,
            tools_in=0,
            mcp_tools_in=0,
            tools_out=None,
            error=str(exc),
        )


def build_stats_record(
    *,
    endpoint: str,
    target_url: str,
    upstream_model: str | None,
    pruning_pipeline: list[str] | None,
    pruning: Any,
    config: dict[str, Any],
    store_full_tools: bool,
) -> Any:
    from db import ProxyRequestRecord, lookup_model_provider, provider_dns_from_url

    provider, provider_dns = lookup_model_provider(upstream_model, config)
    if not provider_dns:
        provider_dns = provider_dns_from_url(target_url)

    tools_accepted_json: str | None = None
    tools_final_json: str | None = None
    if store_full_tools and pruning.tools_accepted is not None:
        from build_index import compact_json

        tools_accepted_json = compact_json(pruning.tools_accepted)
    if store_full_tools and pruning.tools_final is not None:
        from build_index import compact_json

        tools_final_json = compact_json(pruning.tools_final)

    return ProxyRequestRecord(
        endpoint=endpoint,
        tools_in=pruning.tokens_in or 0,
        tool_count_in=pruning.tools_in,
        tool_properties_count_in=pruning.tool_properties_count_in or 0,
        tools_out=pruning.tokens_out or 0,
        tool_count_out=pruning.tools_out or 0,
        tool_properties_count_out=pruning.tool_properties_count_out or 0,
        prune_status=pruning.status,
        pipeline=pruning_pipeline or [],
        upstream_model_name=upstream_model,
        upstream_provider_dns=provider_dns,
        upstream_provider=provider,
        query=pruning.query,
        error=pruning.error,
        tools_accepted_json=tools_accepted_json,
        tools_final_json=tools_final_json,
        pruning_stages=dict(pruning.pruning_token_usage),
    )


def _record_stats_async(
    stats_db: Any,
    record: Any,
) -> None:
    try:
        stats_db.record_proxy_request(record)
    except Exception as exc:
        logger.warning("stats record failed: %s", exc)


def create_app(
    routes: dict[str, tuple[str, str | None]],
    pruning_pipeline: list[str] | None = None,
    debug: bool = False,
    debug_strict: bool = False,
    stats_db: Any | None = None,
    store_full_tools: bool = False,
    config: dict[str, Any] | None = None,
    http2_upstream: bool = False,
) -> Starlette:
    use_http2_upstream = http2_upstream and _http2_package_available()
    if http2_upstream and not use_http2_upstream:
        logger.warning(
            "http2 upstream requested but h2 is not installed; using HTTP/1.1 (pip install h2)",
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=None, http2=use_http2_upstream)
        _app.state.http_client = client
        try:
            yield
        finally:
            await client.aclose()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def proxy(request: Request) -> Response:
        match = resolve_upstream(request.url.path, routes)
        if match is None:
            return Response("Not Found", status_code=404)

        upstream_base, path_suffix, kind, endpoint_name = match
        query = request.url.query
        target_url = f"{upstream_base}{path_suffix}"
        if query:
            target_url = f"{target_url}?{query}"

        content_type = request.headers.get("content-type")
        buffer_body = _needs_request_buffer(
            kind=kind,
            method=request.method,
            debug=debug,
        )
        body = await request.body() if buffer_body else b""
        upstream_model: str | None = None
        if buffer_body and body and content_type and "json" in content_type.lower():
            try:
                upstream_model = json.loads(body).get("model")
            except json.JSONDecodeError:
                pass

        pruning = None
        if buffer_body:
            body, pruning = await transform_request_body(
                body,
                content_type,
                kind,
                pruning_pipeline,
                debug,
            )
        pruning_meta = pruning.to_dict() if pruning is not None else None

        if stats_db is not None and pruning is not None:
            record = build_stats_record(
                endpoint=endpoint_name,
                target_url=target_url,
                upstream_model=upstream_model,
                pruning_pipeline=pruning_pipeline,
                pruning=pruning,
                config=config or {},
                store_full_tools=store_full_tools or debug,
            )
            asyncio.create_task(asyncio.to_thread(_record_stats_async, stats_db, record))
        forward_headers = filter_headers(request.headers)

        if debug:
            if pruning_meta and pruning_meta.get("status") != "applied":
                logger.warning(
                    "pruning %s: %s",
                    pruning_meta.get("status"),
                    pruning_meta.get("error"),
                )
            _print_debug_pruning(pruning_meta)
            snapshot = {
                "method": request.method,
                "path": request.url.path,
                "query": query or None,
                "target_url": target_url,
                "headers": forward_headers,
                "body": _body_for_snapshot(body, content_type),
                "pruning": pruning_meta,
            }
            saved_to = await _save_debug_snapshot(endpoint_name, snapshot)
            logger.info("debug snapshot appended: endpoint=%s path=%s", endpoint_name, request.url.path)
            if debug_strict and pruning_meta and pruning_meta.get("status") != "applied":
                return JSONResponse(
                    {
                        "debug": True,
                        "error": "pruning not applied",
                        "pruning": pruning_meta,
                        "saved_to": str(saved_to),
                    },
                    status_code=502,
                )
            return JSONResponse(
                {
                    "debug": True,
                    "saved_to": str(saved_to),
                    "bytes": len(body),
                    "pruning": pruning_meta,
                },
            )

        client: httpx.AsyncClient = request.app.state.http_client
        return await _forward_upstream(
            client,
            method=request.method,
            url=target_url,
            headers=forward_headers,
            body=body if buffer_body else None,
            stream_request=not buffer_body,
            request=request if not buffer_body else None,
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", proxy, methods=METHODS),
        ],
        lifespan=lifespan,
    )


def resolve_port(config: dict[str, Any], cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    proxy_cfg = config.get("network", {}).get("proxy", {})
    reverse_cfg = _reverse_proxy_cfg(proxy_cfg)
    return int(reverse_cfg.get("port", DEFAULT_PORT))


def _stats_db_path(config: dict[str, Any]) -> str:
    stats_cfg = config.get("stats", {})
    db_cfg = stats_cfg.get("database", {}) if isinstance(stats_cfg, dict) else {}
    path = db_cfg.get("path", "~/.configs/sca/stats.db")
    return str(Path(path).expanduser())


def _run_stats_cli(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from db import StatsDB, empty_totals, format_events, format_totals
    from pricing import compute_stats_costs, empty_costs

    db_path = _stats_db_path(config)
    db = StatsDB.open_for_query(db_path)
    try:
        if args.stats_command == "totals":
            period = getattr(args, "period", "all")
            totals = db.query_totals(period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(totals, db.query_stage_model_tokens(period), config)
                if db is not None
                else empty_costs(config)
            )
            print(format_totals(totals, costs))
        elif args.stats_command == "summary":
            totals = db.query_summary(args.period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(totals, db.query_stage_model_tokens(args.period), config)
                if db is not None
                else empty_costs(config)
            )
            print(format_totals(totals, costs))
        elif args.stats_command == "events":
            events = db.query_events(args.limit) if db is not None else []
            if args.json:
                print(json.dumps(events, indent=2))
            else:
                print(format_events(events))
    finally:
        if db is not None:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Transparent LLM HTTP proxy")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP proxy (default)")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (overrides network.proxy.reverse.port; default {DEFAULT_PORT})",
    )
    serve_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        help="Do not call upstream; append request snapshots to {endpoint}.log",
    )
    serve_parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug, return 502 when tool pruning did not apply",
    )
    serve_parser.add_argument(
        "--http2-upstream",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use HTTP/2 for upstream requests (requires h2; overrides config)",
    )
    serve_parser.add_argument(
        "--http2-serve",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Listen with HTTP/2 via Hypercorn (requires hypercorn[h2] and TLS)",
    )
    serve_parser.add_argument(
        "--ssl-keyfile",
        type=Path,
        default=None,
        help="TLS key for --http2-serve (or HTTPS uvicorn)",
    )
    serve_parser.add_argument(
        "--ssl-certfile",
        type=Path,
        default=None,
        help="TLS certificate for --http2-serve (or HTTPS uvicorn)",
    )

    stats_parser = subparsers.add_parser("stats", help="Query persisted proxy stats")
    stats_sub = stats_parser.add_subparsers(dest="stats_command", required=True)

    stats_totals = stats_sub.add_parser("totals", help="Aggregate token totals")
    stats_totals.add_argument(
        "--period",
        choices=["day", "week", "month", "all"],
        default="all",
    )
    stats_totals.add_argument("--config", type=Path, default=None)

    stats_summary = stats_sub.add_parser("summary", help="Summary for a time period")
    stats_summary.add_argument(
        "--period",
        choices=["day", "week", "month", "all"],
        default="day",
    )
    stats_summary.add_argument("--config", type=Path, default=None)

    stats_events = stats_sub.add_parser("events", help="Recent proxy events")
    stats_events.add_argument("--limit", type=int, default=20)
    stats_events.add_argument("--json", action="store_true")
    stats_events.add_argument("--config", type=Path, default=None)

    # Legacy: flags without subcommand still start the proxy
    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-strict", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "stats":
        config = load_proxy_config(getattr(args, "config", None))
        _run_stats_cli(args, config)
        return

    # Default to serve when no subcommand (backward compatible)
    if args.command is None:
        args.command = "serve"
        if not hasattr(args, "debug"):
            args.debug = False
        if not hasattr(args, "debug_strict"):
            args.debug_strict = False

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    if args.debug:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s:%(name)s: %(message)s",
            force=True,
        )

    config = load_proxy_config(args.config)
    from tool_policies import configure_policies_from_config

    configure_policies_from_config(config)
    proxy_cfg = config["network"]["proxy"]
    routes = build_routes(proxy_cfg)
    pruning_pipeline = _pruning_pipeline_from_config(config)
    port = resolve_port(config, args.port)

    stats_cfg = config.get("stats", {})
    stats_enabled = isinstance(stats_cfg, dict) and stats_cfg.get("enabled", False)
    store_full_tools = isinstance(stats_cfg, dict) and stats_cfg.get("store_full_tools", False)
    stats_db = None
    if stats_enabled:
        try:
            from db import StatsDB

            stats_db = StatsDB.init(_stats_db_path(config))
        except Exception as exc:
            logger.warning("stats database unavailable: %s", exc)

    http2_settings = _proxy_http2_settings(config)
    http2_upstream = (
        args.http2_upstream
        if hasattr(args, "http2_upstream") and args.http2_upstream is not None
        else http2_settings["http2_upstream"]
    )
    http2_serve = (
        args.http2_serve
        if hasattr(args, "http2_serve") and args.http2_serve is not None
        else http2_settings["http2_serve"]
    )
    ssl_keyfile = (
        str(args.ssl_keyfile)
        if getattr(args, "ssl_keyfile", None) is not None
        else http2_settings["ssl_keyfile"]
    )
    ssl_certfile = (
        str(args.ssl_certfile)
        if getattr(args, "ssl_certfile", None) is not None
        else http2_settings["ssl_certfile"]
    )

    app = create_app(
        routes,
        pruning_pipeline,
        debug=args.debug,
        debug_strict=args.debug_strict,
        stats_db=stats_db,
        store_full_tools=store_full_tools,
        config=config,
        http2_upstream=http2_upstream,
    )

    run_proxy_server(
        app,
        host="0.0.0.0",
        port=port,
        http2_serve=http2_serve,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


if __name__ == "__main__":
    main()
