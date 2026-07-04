"""Reverse HTTP proxy with Anthropic and OpenAI request pruning."""

from __future__ import annotations

import asyncio
import base64
import copy
import gzip
import json
import logging
import re
import struct
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    from cyt.proxy.anthropic import PruneResult
    from cyt.proxy.stats import ProxyRequestRecord, StatsDB

from cyt.config import (
    debug_log_max_body_bytes,
    pruning_pipeline_from_config,
    reverse_debug_log_dir,
    reverse_proxy_cfg,
    stats_db_path,
)
from cyt.proxy.pruning_debug import (
    format_decomposed_paths_lines as _format_decomposed_paths_lines,
)
from cyt.proxy.pruning_debug import (
    format_decomposed_table_lines as _format_decomposed_table_lines,
)
from cyt.proxy.pruning_debug import (
    format_removed_chunks_lines as _format_removed_chunks_lines,
)
from cyt.proxy.setup_wizard import normalize_upstream_kind, upstream_entry_endpoint
from cyt.proxy.transport import (
    agent_trace_log_path,
    append_agent_trace_log,
    append_debug_log_block,
    debug_endpoint_proxy_log_path,
    forward_upstream,
    header_content_encoding,
    http2_package_available,
    new_debug_session_id,
    reverse_debug_log_path,
    reverse_debug_original_log_path,
    reverse_debug_proxy_log_path,
    run_hypercorn_async,
    run_uvicorn_async,
    save_debug_snapshot,
    save_original_debug_snapshot,
)
from cyt.proxy.upstream_auth import prepare_forward_headers
from cyt.pruners.remote import PrunerSettingsCache

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

logger = logging.getLogger(__name__)

_debug_request_seq = 0


@dataclass(frozen=True)
class DebugTrace:
    log_path: Path
    session_id: str
    run_id: str

    def log(
        self,
        *,
        hypothesis_id: str,
        location: str,
        message: str,
        data: dict[str, Any],
    ) -> None:
        append_agent_trace_log(
            self.log_path,
            session_id=self.session_id,
            run_id=self.run_id,
            hypothesis_id=hypothesis_id,
            location=location,
            message=message,
            data=data,
        )


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
        frame_end = 5 + length
        payload = bytes(buffer[5:frame_end])
        del buffer[:frame_end]
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
    if runs := _PRINTABLE_RUN.findall(body):
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
    payload: object,
    *,
    original_len: int,
    max_bytes: int | None,
    wrap_key: str,
) -> object:
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
) -> object | None:
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


def body_for_original_snapshot(
    body: bytes,
    content_type: str | None = None,
    *,
    max_bytes: int | None = None,
) -> object | None:
    """Request body as received (no connect decode or decompress); JSON is parsed for logging."""
    if not body:
        return None
    original_len = len(body)
    truncated = body
    if max_bytes is not None and len(body) > max_bytes:
        truncated = body[:max_bytes]
    if content_type and "json" in content_type.lower():
        json_value = _snapshot_decoded_json(
            truncated,
            original_len=original_len,
            max_bytes=max_bytes,
        )
        if json_value is not None:
            return json_value
    try:
        payload: object = truncated.decode("utf-8")
    except UnicodeDecodeError:
        payload = {"_base64": base64.standard_b64encode(truncated).decode("ascii")}
    return _maybe_truncated_payload(
        payload,
        original_len=original_len,
        max_bytes=max_bytes,
        wrap_key="_body",
    )


def body_for_snapshot(
    body: bytes,
    content_type: str | None,
    *,
    content_encoding: str | None = None,
    max_bytes: int | None = None,
) -> object | None:
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
    upstreams = {
        upstream_entry_endpoint(item): item
        for item in reverse_cfg.get("upstreams", [])
        if isinstance(item, dict)
    }
    routes: dict[str, tuple[str, str | None]] = {}
    for endpoint in reverse_cfg.get("endpoints", []):
        if endpoint not in upstreams:
            raise ValueError(f"No upstream configured for endpoint: {endpoint}")
        entry = upstreams[endpoint]
        kind_raw = entry.get("kind")
        kind = normalize_upstream_kind(str(kind_raw)) if kind_raw is not None else None
        url = entry.get("url") or entry.get("host_url") or entry.get("base_url")
        if not url:
            raise ValueError(f"No url configured for upstream: {endpoint}")
        routes[f"/{endpoint}"] = (str(url).rstrip("/"), kind)
    if not routes:
        raise ValueError("No proxy endpoints configured")
    return routes


def health_endpoint_names(routes: dict[str, tuple[str, str | None]]) -> list[str]:
    """Return configured reverse-proxy endpoint names for ``/health``."""
    return sorted(route.lstrip("/") for route in routes)


def resolve_upstream(
    path: str,
    routes: dict[str, tuple[str, str | None]],
) -> tuple[str, str, str | None, str] | None:
    prefixes: list[str] = sorted(routes, key=lambda route: len(route), reverse=True)
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            prefix_len = len(prefix)
            suffix = path[prefix_len:] if path != prefix else ""
            upstream_base, kind = routes[prefix]
            endpoint_name = prefix.lstrip("/")
            return upstream_base, suffix, kind, endpoint_name
    return None


def is_startup_probe(method: str, path_suffix: str) -> bool:
    """True for Claude Code gateway reachability checks (HEAD on endpoint root)."""
    return method.upper() == "HEAD" and path_suffix in ("", "/")


async def upstream_reachable(
    client: httpx.AsyncClient,
    upstream_base: str,
    headers: dict[str, str],
) -> bool:
    """Return True when upstream responds (connection errors => False)."""
    import httpx

    url = upstream_base.rstrip("/") or upstream_base
    try:
        async with client.stream("HEAD", url, headers=headers):
            return True
    except httpx.HTTPError:
        return False


async def _handle_startup_probe(
    request: Request,
    *,
    upstream_base: str,
    path_suffix: str,
    endpoint_name: str,
    config: dict[str, Any] | None,
) -> Response | None:
    if not is_startup_probe(request.method, path_suffix):
        return None
    from starlette.responses import Response

    client: httpx.AsyncClient = request.app.state.http_client
    headers = prepare_forward_headers(
        request.headers,
        config=config,
        endpoint_name=endpoint_name,
    )
    if await upstream_reachable(client, upstream_base, headers):
        return Response(status_code=200)
    return Response("Upstream unreachable", status_code=502)


def _needs_request_buffer(
    *,
    kind: str | None,
    method: str,
    debug: bool,
) -> bool:
    if debug:
        return True
    if kind not in ("anthropic", "openai"):
        return False
    return method.upper() in BODY_METHODS


def _next_debug_request_seq() -> int:
    global _debug_request_seq
    _debug_request_seq += 1
    return _debug_request_seq


def _format_debug_pruning_lines(
    pruning: dict[str, Any] | None,
    *,
    request_seq: int,
    endpoint: str | None = None,
    request_path: str | None = None,
    include_paths: bool = True,
) -> list[str]:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    header_parts = [f"debug request #{request_seq} @ {stamp}"]
    if endpoint:
        header_parts.append(f"endpoint={endpoint}")
    if request_path:
        header_parts.append(f"path={request_path}")
    lines: list[str] = [
        "",
        f"--- {' | '.join(header_parts)} ---",
        "",
    ]
    if pruning is None:
        lines.extend(["user query:", "(none — body was not transformed)", ""])
    else:
        query = pruning.get("query")
        lines.extend(["user query:", ""])
        lines.append(query if query else "(none extracted)")
        lines.append("")
        lines.extend(_format_decomposed_table_lines(pruning))
        if include_paths:
            lines.extend(_format_decomposed_paths_lines(pruning))
            lines.extend(_format_removed_chunks_lines(pruning))
        if status := pruning.get("status"):
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
        if pruning_model_tokens := pruning.get("pruning_model_tokens") or {}:
            parts = ", ".join(
                f"{stage}={pruning_model_tokens[stage]}"
                for stage in ("rerank", "bm25", "llm")
                if stage in pruning_model_tokens
            )
            lines.append(f"pruning model tokens: {parts}")
        if error := pruning.get("error"):
            lines.append(f"pruning error: {error}")
    lines.append("")
    return lines


def _print_debug_pruning(
    pruning: dict[str, Any] | None,
    *,
    request_seq: int,
    endpoint: str | None = None,
    request_path: str | None = None,
    proxy_log_path: Path | None = None,
) -> None:
    summary_text = "\n".join(
        _format_debug_pruning_lines(
            pruning,
            request_seq=request_seq,
            endpoint=endpoint,
            request_path=request_path,
            include_paths=False,
        ),
    )
    print(summary_text, flush=True)
    if proxy_log_path is not None:
        verbose_text = "\n".join(
            _format_debug_pruning_lines(
                pruning,
                request_seq=request_seq,
                endpoint=endpoint,
                request_path=request_path,
                include_paths=True,
            ),
        )
        append_debug_log_block(proxy_log_path, label="pruning", content=verbose_text)


def _input_tools_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Deep copy of request ``tools`` before pruning transforms the payload."""
    raw = payload.get("tools")
    if not isinstance(raw, list):
        return []
    return copy.deepcopy(raw)


def _pruning_meta_for_debug(
    pruning_meta: dict[str, Any] | None,
    input_tools: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if pruning_meta is None or input_tools is None:
        return pruning_meta
    return {**pruning_meta, "input": {"tools": input_tools}}


async def transform_request_body(
    body: bytes,
    content_type: str | None,
    kind: str | None,
    pruning_pipeline: list[str] | None = None,
    debug: bool = False,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[bytes, Any | None, list[dict[str, Any]] | None, Any | None]:
    from cyt.skills.proxy_inject import SkillsProxyInjectMeta

    if not body or kind not in ("anthropic", "openai"):
        return body, None, None, None
    if not content_type or "json" not in content_type.lower():
        return body, None, None, None
    input_tools: list[dict[str, Any]] | None = None
    skills_meta: SkillsProxyInjectMeta | None = None
    try:
        payload = json.loads(body)
        if debug:
            input_tools = _input_tools_from_payload(payload)
        if kind == "anthropic":
            from cyt.proxy.anthropic import PruneResult, transform_anthropic_request

            transformed, pruning, skills_meta = await asyncio.to_thread(
                transform_anthropic_request,
                payload,
                pruning_pipeline,
                capture_decomposed_catalog=debug,
                config=config,
                pruner_settings=pruner_settings,
            )
        else:
            from cyt.proxy.openai_responses import transform_openai_request

            transformed, pruning, skills_meta = await asyncio.to_thread(
                transform_openai_request,
                payload,
                pruning_pipeline,
                capture_decomposed_catalog=debug,
                config=config,
                pruner_settings=pruner_settings,
            )
        return json.dumps(transformed).encode(), pruning, input_tools, skills_meta
    except json.JSONDecodeError:
        return body, None, None, None
    except Exception as exc:
        logger.warning("%s transform failed: %s", kind, exc)
        from cyt.proxy.anthropic import PruneResult

        return (
            body,
            PruneResult(
                tools=None,
                status="failed",
                query=None,
                tools_in=0,
                mcp_tools_in=0,
                tools_out=None,
                error=str(exc),
            ),
            input_tools,
            None,
        )


def build_stats_record(
    *,
    endpoint: str,
    target_url: str,
    upstream_model: str | None,
    pruning_pipeline: list[str] | None,
    pruning: PruneResult,
    config: dict[str, Any],
    store_full_tools: bool,
) -> ProxyRequestRecord:
    from cyt.proxy.stats import (
        ProxyRequestRecord,
        lookup_model_provider,
        lookup_provider_from_dns,
        provider_dns_from_url,
    )

    provider, provider_dns = lookup_model_provider(upstream_model, config)
    if not provider_dns:
        provider_dns = provider_dns_from_url(target_url)
    if not provider:
        provider = lookup_provider_from_dns(provider_dns, config)

    tools_accepted_json: str | None = None
    tools_final_json: str | None = None
    if store_full_tools and pruning.tools_accepted is not None:
        from cyt.indexer.tokens import compact_json

        tools_accepted_json = compact_json(pruning.tools_accepted)
    if store_full_tools and pruning.tools_final is not None:
        from cyt.indexer.tokens import compact_json

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


def _record_stats_async(stats_db: StatsDB, record: ProxyRequestRecord) -> None:
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


def _schedule_stats_record(stats_db: StatsDB, record: ProxyRequestRecord) -> None:
    task = asyncio.create_task(asyncio.to_thread(_record_stats_async, stats_db, record))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _record_skills_injection_async(
    *,
    query: str,
    model_name: str,
    skills_in: int,
    config: dict[str, Any],
    request_tokens: int = 0,
    skills_final_md: str | None = None,
) -> None:
    from cyt.skills.stats import record_skills_injection

    try:
        record_skills_injection(
            query=query,
            model_name=model_name,
            skills_in=skills_in,
            request_tokens=request_tokens,
            inject_path="proxy",
            skills_final_md=skills_final_md,
            config=config,
        )
    except Exception as exc:
        logger.warning("skills injection stats record failed: %s", exc)


def _schedule_skills_injection_record(
    *,
    query: str,
    model_name: str,
    skills_in: int,
    config: dict[str, Any],
    request_tokens: int = 0,
    skills_final_md: str | None = None,
) -> None:
    task = asyncio.create_task(
        asyncio.to_thread(
            _record_skills_injection_async,
            query=query,
            model_name=model_name,
            skills_in=skills_in,
            config=config,
            request_tokens=request_tokens,
            skills_final_md=skills_final_md,
        ),
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _save_debug_original_request(
    *,
    request: Request,
    query: str,
    target_url: str,
    endpoint_name: str,
    request_path: str,
    body: bytes,
    request_seq: int,
    debug_log_max_body_bytes: int | None,
    debug_log_dir: Path | None,
) -> Path:
    content_type = request.headers.get("content-type")
    log_path = reverse_debug_original_log_path(endpoint_name, debug_log_dir=debug_log_dir)
    snapshot = {
        "debug_request_seq": request_seq,
        "method": request.method,
        "path": request_path,
        "query": query or None,
        "target_url": target_url,
        "timestamp": datetime.now(UTC).isoformat(),
        "headers": dict(request.headers),
        "body": body_for_original_snapshot(
            body,
            content_type,
            max_bytes=debug_log_max_body_bytes,
        ),
    }
    saved_to = await save_original_debug_snapshot(
        log_path,
        snapshot,
        max_bytes=debug_log_max_body_bytes,
    )
    logger.info(
        "debug original request appended: endpoint=%s path=%s file=%s",
        endpoint_name,
        request_path,
        saved_to,
    )
    return saved_to


async def _handle_debug_snapshot(
    *,
    request: Request,
    query: str,
    target_url: str,
    endpoint_name: str,
    request_path: str,
    forward_headers: dict[str, str],
    body: bytes,
    content_type: str | None,
    pruning_meta: dict[str, Any] | None,
    request_seq: int,
    debug_terminate: bool,
    debug_strict: bool,
    debug_log_max_body_bytes: int | None,
    debug_log_dir: Path | None,
    debug_trace: DebugTrace | None,
) -> Response | None:
    if pruning_meta and pruning_meta.get("status") != "applied":
        logger.warning(
            "pruning %s: %s",
            pruning_meta.get("status"),
            pruning_meta.get("error"),
        )
    log_path = reverse_debug_log_path(endpoint_name, debug_log_dir=debug_log_dir)
    proxy_log_path = reverse_debug_proxy_log_path(endpoint_name, debug_log_dir=debug_log_dir)
    _print_debug_pruning(
        pruning_meta,
        request_seq=request_seq,
        endpoint=endpoint_name,
        request_path=request_path,
        proxy_log_path=proxy_log_path,
    )
    snapshot = {
        "debug_request_seq": request_seq,
        "method": request.method,
        "path": request_path,
        "query": query or None,
        "target_url": target_url,
        "timestamp": datetime.now(UTC).isoformat(),
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
        log_path,
        snapshot,
        max_bytes=debug_log_max_body_bytes,
    )
    logger.info(
        "debug snapshot appended: endpoint=%s path=%s",
        endpoint_name,
        request_path,
    )
    if not debug_terminate:
        return None
    return await _debug_terminate_response(
        endpoint_name=endpoint_name,
        request_path=request_path,
        target_url=target_url,
        debug_strict=debug_strict,
        pruning_meta=pruning_meta,
        saved_to=saved_to,
        body=body,
        debug_trace=debug_trace,
    )


def _log_proxy_request_entry(debug_trace: DebugTrace | None, request: Request) -> None:
    if debug_trace is None:
        return
    debug_trace.log(
        hypothesis_id="D",
        location="reverse.py:_proxy_request:entry",
        message="valid HTTP request reached proxy handler",
        data={
            "method": request.method,
            "path": request.url.path,
            "scheme": request.url.scheme,
        },
    )


async def _process_buffered_proxy_body(
    *,
    request: Request,
    body: bytes,
    content_type: str | None,
    kind: str | None,
    query: str,
    target_url: str,
    endpoint_name: str,
    request_path: str,
    pruning_pipeline: list[str] | None,
    debug: bool,
    debug_log_max_body_bytes: int | None,
    debug_log_dir: Path | None,
    config: dict[str, Any] | None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[
    bytes,
    Any | None,
    list[dict[str, Any]] | None,
    dict[str, Any] | None,
    int | None,
    Any | None,
]:
    debug_request_seq: int | None = None
    if debug:
        debug_request_seq = _next_debug_request_seq()
        await _save_debug_original_request(
            request=request,
            query=query,
            target_url=target_url,
            endpoint_name=endpoint_name,
            request_path=request_path,
            body=body,
            request_seq=debug_request_seq,
            debug_log_max_body_bytes=debug_log_max_body_bytes,
            debug_log_dir=debug_log_dir,
        )

    pruning = None
    input_tools: list[dict[str, Any]] | None = None
    debug_log_token = None
    if debug:
        debug_log_token = debug_endpoint_proxy_log_path.set(
            reverse_debug_proxy_log_path(endpoint_name, debug_log_dir=debug_log_dir),
        )
    try:
        from cyt.proxy.upstream_auth import (
            apply_request_auth_to_pruner_settings,
            request_pruner_settings_scope,
        )

        effective_pruner_settings = apply_request_auth_to_pruner_settings(
            pruner_settings,
            request.headers,
            config,
            endpoint_name,
        )
        with request_pruner_settings_scope(effective_pruner_settings):
            body, pruning, input_tools, skills_meta = await transform_request_body(
                body,
                content_type,
                kind,
                pruning_pipeline,
                debug,
                config=config,
                pruner_settings=effective_pruner_settings,
            )
    finally:
        if debug_log_token is not None:
            debug_endpoint_proxy_log_path.reset(debug_log_token)

    pruning_meta = pruning.to_dict() if pruning is not None else None
    if debug:
        pruning_meta = _pruning_meta_for_debug(pruning_meta, input_tools)
    return body, pruning, input_tools, pruning_meta, debug_request_seq, skills_meta


def _log_proxy_forward_upstream(
    debug_trace: DebugTrace | None,
    *,
    endpoint_name: str,
    request_path: str,
    target_url: str,
    buffer_body: bool,
) -> None:
    if debug_trace is None:
        return
    debug_trace.log(
        hypothesis_id="B",
        location="reverse.py:_proxy_request:forward_upstream",
        message="forwarding request to upstream",
        data={
            "endpoint": endpoint_name,
            "path": request_path,
            "target_url": target_url,
            "buffer_body": buffer_body,
        },
    )


async def _debug_terminate_response(
    *,
    endpoint_name: str,
    request_path: str,
    target_url: str,
    debug_strict: bool,
    pruning_meta: dict[str, Any] | None,
    saved_to: Path,
    body: bytes,
    debug_trace: DebugTrace | None = None,
) -> JSONResponse:
    from starlette.responses import JSONResponse

    if debug_trace is not None:
        debug_trace.log(
            hypothesis_id="A",
            location="reverse.py:_debug_terminate_response",
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
    debug_log_dir: Path | None,
    debug_trace: DebugTrace | None,
    stats_db: StatsDB | None,
    store_full_tools: bool,
    config: dict[str, Any] | None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> Response:
    from starlette.responses import Response

    _log_proxy_request_entry(debug_trace, request)
    match = resolve_upstream(request.url.path, routes)
    if match is None:
        return Response("Not Found", status_code=404)

    upstream_base, path_suffix, kind, endpoint_name = match
    if probe := await _handle_startup_probe(
        request,
        upstream_base=upstream_base,
        path_suffix=path_suffix,
        endpoint_name=endpoint_name,
        config=config,
    ):
        return probe

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
    pruning_meta: dict[str, Any] | None = None
    debug_request_seq: int | None = None
    skills_meta: Any | None = None
    if buffer_body:
        (
            body,
            pruning,
            _,
            pruning_meta,
            debug_request_seq,
            skills_meta,
        ) = await _process_buffered_proxy_body(
            request=request,
            body=body,
            content_type=content_type,
            kind=kind,
            query=query,
            target_url=target_url,
            endpoint_name=endpoint_name,
            request_path=request.url.path,
            pruning_pipeline=pruning_pipeline,
            debug=debug,
            debug_log_max_body_bytes=debug_log_max_body_bytes,
            debug_log_dir=debug_log_dir,
            config=config,
            pruner_settings=pruner_settings,
        )

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

    if (
        skills_meta is not None
        and skills_meta.skills_in > 0
        and skills_meta.query
        and upstream_model
    ):
        _schedule_skills_injection_record(
            query=skills_meta.query,
            model_name=upstream_model,
            skills_in=skills_meta.skills_in,
            request_tokens=int(getattr(skills_meta, "request_tokens", 0) or 0),
            skills_final_md=skills_meta.skills_final_md if debug else None,
            config=config or {},
        )
    forward_headers = prepare_forward_headers(
        request.headers,
        config=config,
        endpoint_name=endpoint_name,
    )

    if debug and debug_request_seq is not None:
        early = await _handle_debug_snapshot(
            request=request,
            query=query,
            target_url=target_url,
            endpoint_name=endpoint_name,
            request_path=request.url.path,
            forward_headers=forward_headers,
            body=body,
            content_type=content_type,
            pruning_meta=pruning_meta,
            request_seq=debug_request_seq,
            debug_terminate=debug_terminate,
            debug_strict=debug_strict,
            debug_log_max_body_bytes=debug_log_max_body_bytes,
            debug_log_dir=debug_log_dir,
            debug_trace=debug_trace,
        )
        if early is not None:
            return early

    client: httpx.AsyncClient = request.app.state.http_client
    _log_proxy_forward_upstream(
        debug_trace,
        endpoint_name=endpoint_name,
        request_path=request.url.path,
        target_url=target_url,
        buffer_body=buffer_body,
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
    debug_log_dir: Path | None = None,
    debug_trace: DebugTrace | None = None,
    stats_db: StatsDB | None = None,
    store_full_tools: bool = False,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    http2_upstream: bool = False,
    launch_agent: str | None = None,
) -> Starlette:
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    use_http2_upstream = http2_upstream and http2_package_available()
    if http2_upstream and not use_http2_upstream:
        logger.warning(
            "http2 upstream requested but h2 is not installed; using HTTP/1.1 (pip install h2)",
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=None, http2=use_http2_upstream)
        app.state.http_client = client
        app.state.pruner_settings = pruner_settings
        if config is not None:
            app.state.cyt_config = config
            from cyt.cache import warm_caches

            warm_caches(config)
        try:
            yield
        finally:
            await client.aclose()

    async def health(_: Request) -> JSONResponse:
        payload: dict[str, Any] = {
            "name": "cyt",
            "status": "ok",
            "hook": True,
            "endpoints": health_endpoint_names(routes),
            "debug": debug,
            "debug_dry_run": debug_terminate,
        }
        if launch_agent is not None:
            payload["agent"] = launch_agent
        return JSONResponse(payload)

    from cyt.hook.http_server import hook_inject

    async def proxy(request: Request) -> Response:
        return await _proxy_request(
            request,
            routes=routes,
            pruning_pipeline=pruning_pipeline,
            debug=debug,
            debug_terminate=debug_terminate,
            debug_strict=debug_strict,
            debug_log_max_body_bytes=debug_log_max_body_bytes,
            debug_log_dir=debug_log_dir,
            debug_trace=debug_trace,
            stats_db=stats_db,
            store_full_tools=store_full_tools,
            config=config,
            pruner_settings=pruner_settings,
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/hook/inject", hook_inject, methods=["POST"]),
            Route("/{path:path}", proxy, methods=METHODS),
        ],
        lifespan=lifespan,
    )


def _ssl_file_exists(path: str | None) -> bool:
    return bool(path and Path(path).is_file())


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
    pruner_settings: PrunerSettingsCache | None = None,
    launch_agent: str | None = None,
) -> None:
    from cyt.config import require_proxy_env

    require_proxy_env(config)

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

    debug_log_dir: Path | None = None
    debug_trace: DebugTrace | None = None
    if debug:
        debug_log_dir = reverse_debug_log_dir(config)
        debug_log_dir.mkdir(parents=True, exist_ok=True)
        session_id = new_debug_session_id()
        debug_trace = DebugTrace(
            log_path=agent_trace_log_path(debug_log_dir, session_id),
            session_id=session_id,
            run_id=session_id,
        )
        debug_trace.log(
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
                "ssl_key_exists": _ssl_file_exists(ssl_keyfile),
                "ssl_cert_exists": _ssl_file_exists(ssl_certfile),
                "transport": "hypercorn+tls" if http2_serve else "uvicorn+plain-http",
            },
        )

    app = create_app(
        routes,
        pruning_pipeline,
        debug=debug,
        debug_terminate=debug_terminate,
        debug_strict=debug_strict,
        debug_log_max_body_bytes=debug_log_max_body_bytes(config),
        debug_log_dir=debug_log_dir,
        debug_trace=debug_trace,
        stats_db=stats_db,
        store_full_tools=store_full_tools,
        config=config,
        pruner_settings=pruner_settings,
        http2_upstream=http2_upstream,
        launch_agent=launch_agent,
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
