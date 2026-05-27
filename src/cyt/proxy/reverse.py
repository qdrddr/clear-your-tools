"""Reverse HTTP proxy for LLM API endpoints (path-based routing, anthropic pruning)."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import struct
import time
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cyt.config import (
    debug_log_max_body_bytes,
    pruning_pipeline_from_config,
    reverse_proxy_cfg,
    stats_db_path,
)
from cyt.proxy.transport import (
    filter_headers,
    forward_upstream,
    header_content_encoding,
    http2_package_available,
    reverse_debug_log_path,
    run_hypercorn_async,
    run_uvicorn_async,
    save_debug_snapshot,
)

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

logger = logging.getLogger(__name__)

_DEBUG_LOG_PATH = Path(__file__).resolve().parents[3] / ".debug" / "debug-308477.log"


def _agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "308477",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
    # #endregion


CONNECT_FLAG_COMPRESSED = 0x01


@dataclass(frozen=True)
class ConnectFrame:
    compressed: bool
    payload: bytes


def _parse_connect_frames(buffer: bytearray) -> list[ConnectFrame]:
    frames: list[ConnectFrame] = []
    while len(buffer) >= 5:
        flags = buffer[0]
        length = struct.unpack(">I", buffer[1:5])[0]
        if len(buffer) < 5 + length:
            break
        payload = bytes(buffer[5 : 5 + length])
        del buffer[: 5 + length]
        frames.append(
            ConnectFrame(compressed=bool(flags & CONNECT_FLAG_COMPRESSED), payload=payload),
        )
    return frames


def _decompress_connect_frame_payload(
    frame: ConnectFrame,
    *,
    content_encoding: str | None = None,
) -> bytes:
    payload = frame.payload
    if frame.compressed:
        payload = gzip.decompress(payload)
    elif content_encoding and "gzip" in content_encoding.lower() and payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except OSError:
            pass
    return payload


def _decode_connect_payload(raw: bytes, *, content_encoding: str | None = None) -> bytes:
    if not raw:
        return raw
    buf = bytearray(raw)
    frames = _parse_connect_frames(buf)
    if frames and not buf:
        return b"".join(
            _decompress_connect_frame_payload(frame, content_encoding=content_encoding)
            for frame in frames
        )
    if content_encoding and "gzip" in content_encoding.lower():
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    return raw


def _decompress_single_encoding(encoding: str, body: bytes) -> bytes | None:
    lower = encoding.strip().lower()
    if lower in {"identity", ""}:
        return None
    if lower in {"gzip", "x-gzip"}:
        return gzip.decompress(body)
    if lower == "deflate":
        return zlib.decompress(body)
    if lower == "br":
        try:
            import importlib

            brotli = importlib.import_module("brotli")
        except ImportError:
            return body
        return brotli.decompress(body)  # type: ignore[no-any-return]
    return None


def _decompress_body(body: bytes, content_encoding: str | None) -> bytes:
    if not body:
        return body
    if content_encoding:
        for enc in content_encoding.split(","):
            decompressed = _decompress_single_encoding(enc, body)
            if decompressed is not None:
                return decompressed
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError:
            pass
    return body


_PRINTABLE_RUN = re.compile(rb"[\x09\x0a\x0d\x20-\x7e\xc2-\xf4][\x20-\x7e\x80-\xbf]*")


def _extract_printable_text(body: bytes) -> str:
    runs = _PRINTABLE_RUN.findall(body)
    if runs:
        return "\n".join(part.decode("utf-8", errors="replace") for part in runs)
    return body.decode("utf-8", errors="replace")


def _bytes_to_log_text(body: bytes, content_type: str | None = None) -> str:
    if content_type and any(
        token in content_type.lower() for token in ("proto", "protobuf", "octet-stream")
    ):
        return _extract_printable_text(body)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return _extract_printable_text(body)


def _maybe_truncated_payload(
    payload: Any,
    *,
    original_len: int,
    max_bytes: int | None,
    wrap_key: str,
) -> Any:
    if max_bytes is not None and original_len > max_bytes:
        return {
            wrap_key: payload,
            "_truncated": True,
            "_original_bytes": original_len,
        }
    return payload


def _snapshot_decoded_json(
    decoded: bytes,
    *,
    original_len: int,
    max_bytes: int | None,
) -> Any | None:
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    return _maybe_truncated_payload(
        value,
        original_len=original_len,
        max_bytes=max_bytes,
        wrap_key="_json",
    )


def body_for_snapshot(
    body: bytes,
    content_type: str | None,
    *,
    content_encoding: str | None = None,
    max_bytes: int | None = None,
) -> Any:
    if not body:
        return None
    original_len = len(body)
    truncated = body
    if max_bytes is not None and len(body) > max_bytes:
        truncated = body[:max_bytes]
    decoded = _decode_connect_payload(truncated, content_encoding=content_encoding)
    if decoded is truncated and content_encoding:
        decoded = _decompress_body(decoded, content_encoding)
    elif decoded is truncated:
        decoded = _decompress_body(decoded, content_encoding)
    if content_type and "json" in content_type.lower():
        json_value = _snapshot_decoded_json(
            decoded,
            original_len=original_len,
            max_bytes=max_bytes,
        )
        if json_value is not None:
            return json_value
    if content_type and any(
        token in content_type.lower() for token in ("proto", "protobuf", "connect")
    ):
        text = _extract_printable_text(decoded)
        if text.strip():
            return _maybe_truncated_payload(
                text,
                original_len=original_len,
                max_bytes=max_bytes,
                wrap_key="_text",
            )
    text = _bytes_to_log_text(decoded, content_type)
    return _maybe_truncated_payload(
        text,
        original_len=original_len,
        max_bytes=max_bytes,
        wrap_key="_text",
    )


def build_routes(proxy_cfg: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    upstreams = {item["upstream"]: item for item in reverse_cfg.get("upstreams", [])}
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
    prefixes: list[str] = sorted(routes, key=lambda route: len(route), reverse=True)
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            suffix = path[len(prefix) :] if path != prefix else ""
            upstream_base, kind = routes[prefix]
            endpoint_name = prefix.lstrip("/")
            return upstream_base, suffix, kind, endpoint_name
    return None


def _needs_request_buffer(
    *,
    kind: str | None,
    method: str,
    debug: bool,
) -> bool:
    if debug:
        return True
    if kind != "anthropic":
        return False
    return method.upper() in BODY_METHODS


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
    body = [
        f"{stage:<{col_stage}}  {json_n:>{col_json}}  {md_n:>{col_md}}"
        for stage, json_n, md_n in rows
    ]
    return ["Decomposed items:", header, sep, *body]


def _format_decomposed_paths_lines(pruning: dict[str, Any]) -> list[str]:
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
        from cyt.proxy.anthropic import PruneResult, transform_anthropic_request

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
        from cyt.proxy.anthropic import PruneResult

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
    from cyt.proxy.stats import ProxyRequestRecord, lookup_model_provider, provider_dns_from_url

    provider, provider_dns = lookup_model_provider(upstream_model, config)
    if not provider_dns:
        provider_dns = provider_dns_from_url(target_url)

    tools_accepted_json: str | None = None
    tools_final_json: str | None = None
    if store_full_tools and pruning.tools_accepted is not None:
        from cyt.indexer.build import compact_json

        tools_accepted_json = compact_json(pruning.tools_accepted)
    if store_full_tools and pruning.tools_final is not None:
        from cyt.indexer.build import compact_json

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


def _record_stats_async(stats_db: Any, record: Any) -> None:
    try:
        stats_db.record_proxy_request(record)
    except Exception as exc:
        logger.warning("stats record failed: %s", exc)


def _extract_upstream_model(
    body: bytes,
    content_type: str | None,
    *,
    buffer_body: bool,
) -> str | None:
    if not buffer_body or not body or not content_type or "json" not in content_type.lower():
        return None
    try:
        model = json.loads(body).get("model")
    except json.JSONDecodeError:
        return None
    return str(model) if model is not None else None


def _schedule_stats_record(stats_db: Any, record: Any) -> None:
    task = asyncio.create_task(asyncio.to_thread(_record_stats_async, stats_db, record))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _debug_terminate_response(
    *,
    endpoint_name: str,
    request_path: str,
    target_url: str,
    debug_strict: bool,
    pruning_meta: dict[str, Any] | None,
    saved_to: Path,
    body: bytes,
) -> JSONResponse:
    _agent_debug_log(
        hypothesis_id="A",
        location="proxy_reverse.py:proxy:debug_return",
        message="returning debug JSONResponse instead of upstream",
        data={
            "endpoint": endpoint_name,
            "path": request_path,
            "target_url": target_url,
            "response_preview": '{"debug":true,...}',
        },
    )
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


async def _proxy_request(
    request: Request,
    *,
    routes: dict[str, tuple[str, str | None]],
    pruning_pipeline: list[str] | None,
    debug: bool,
    debug_terminate: bool,
    debug_strict: bool,
    debug_log_max_body_bytes: int | None,
    stats_db: Any | None,
    store_full_tools: bool,
    config: dict[str, Any] | None,
) -> Response:
    # #region agent log
    _agent_debug_log(
        hypothesis_id="D",
        location="reverse.py:_proxy_request:entry",
        message="valid HTTP request reached proxy handler",
        data={
            "method": request.method,
            "path": request.url.path,
            "scheme": request.url.scheme,
        },
    )
    # #endregion
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
    upstream_model = _extract_upstream_model(body, content_type, buffer_body=buffer_body)

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
        _schedule_stats_record(stats_db, record)
    forward_headers = filter_headers(dict(request.headers))

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
            "body": body_for_snapshot(
                body,
                content_type,
                content_encoding=header_content_encoding(forward_headers),
                max_bytes=debug_log_max_body_bytes,
            ),
            "pruning": pruning_meta,
        }
        saved_to = await save_debug_snapshot(
            reverse_debug_log_path(endpoint_name),
            snapshot,
            max_bytes=debug_log_max_body_bytes,
        )
        logger.info(
            "debug snapshot appended: endpoint=%s path=%s",
            endpoint_name,
            request.url.path,
        )
        if debug_terminate:
            return await _debug_terminate_response(
                endpoint_name=endpoint_name,
                request_path=request.url.path,
                target_url=target_url,
                debug_strict=debug_strict,
                pruning_meta=pruning_meta,
                saved_to=saved_to,
                body=body,
            )

    client: httpx.AsyncClient = request.app.state.http_client
    _agent_debug_log(
        hypothesis_id="B",
        location="proxy_reverse.py:proxy:forward_upstream",
        message="forwarding request to upstream",
        data={
            "endpoint": endpoint_name,
            "path": request.url.path,
            "target_url": target_url,
            "buffer_body": buffer_body,
        },
    )
    return await forward_upstream(
        client,
        method=request.method,
        url=target_url,
        headers=forward_headers,
        body=body if buffer_body else None,
        stream_request=not buffer_body,
        request=request if not buffer_body else None,
    )


def create_app(
    routes: dict[str, tuple[str, str | None]],
    pruning_pipeline: list[str] | None = None,
    debug: bool = False,
    debug_terminate: bool = False,
    debug_strict: bool = False,
    debug_log_max_body_bytes: int | None = None,
    stats_db: Any | None = None,
    store_full_tools: bool = False,
    config: dict[str, Any] | None = None,
    http2_upstream: bool = False,
) -> Starlette:
    use_http2_upstream = http2_upstream and http2_package_available()
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
        return await _proxy_request(
            request,
            routes=routes,
            pruning_pipeline=pruning_pipeline,
            debug=debug,
            debug_terminate=debug_terminate,
            debug_strict=debug_strict,
            debug_log_max_body_bytes=debug_log_max_body_bytes,
            stats_db=stats_db,
            store_full_tools=store_full_tools,
            config=config,
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", proxy, methods=METHODS),
        ],
        lifespan=lifespan,
    )


async def serve_reverse_async(
    config: dict[str, Any],
    *,
    host: str,
    port: int,
    debug: bool,
    debug_terminate: bool,
    debug_strict: bool,
    http2_upstream: bool,
    http2_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    proxy_cfg = config["network"]["proxy"]
    routes = build_routes(proxy_cfg)
    pruning_pipeline = pruning_pipeline_from_config(config)

    stats_cfg = config.get("stats", {})
    stats_enabled = isinstance(stats_cfg, dict) and stats_cfg.get("enabled", False)
    store_full_tools = isinstance(stats_cfg, dict) and stats_cfg.get("store_full_tools", False)
    stats_db = None
    if stats_enabled:
        try:
            from cyt.proxy.stats import StatsDB

            stats_db = StatsDB.init(stats_db_path(config))
        except Exception as exc:
            logger.warning("stats database unavailable: %s", exc)

    # #region agent log
    _agent_debug_log(
        hypothesis_id="A",
        location="reverse.py:serve_reverse_async:startup",
        message="proxy server starting",
        data={
            "host": host,
            "port": port,
            "http2_serve": http2_serve,
            "http2_upstream": http2_upstream,
            "ssl_keyfile": ssl_keyfile,
            "ssl_certfile": ssl_certfile,
            "ssl_key_exists": bool(ssl_keyfile and Path(ssl_keyfile).exists()),
            "ssl_cert_exists": bool(ssl_certfile and Path(ssl_certfile).exists()),
            "transport": "hypercorn+tls" if http2_serve else "uvicorn+plain-http",
        },
    )
    # #endregion

    app = create_app(
        routes,
        pruning_pipeline,
        debug=debug,
        debug_terminate=debug_terminate,
        debug_strict=debug_strict,
        debug_log_max_body_bytes=debug_log_max_body_bytes(config),
        stats_db=stats_db,
        store_full_tools=store_full_tools,
        config=config,
        http2_upstream=http2_upstream,
    )

    if http2_serve:
        await run_hypercorn_async(
            app,
            host=host,
            port=port,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        )
        return

    await run_uvicorn_async(
        app,
        host=host,
        port=port,
        ssl_keyfile=None,
        ssl_certfile=None,
    )
