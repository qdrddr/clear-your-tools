"""Central CLI and shared utilities for reverse and MITM forward proxies."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import re
import sys
import zlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp

DEFAULT_PORT = 8000
DEFAULT_FORWARD_PORT = 8835
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

logger = logging.getLogger(__name__)


def http2_package_available() -> bool:
    try:
        import h2
    except ImportError:
        return False
    return True


def load_proxy_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).with_name("config.yaml")
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid proxy config in {config_path}")
    return data


def reverse_proxy_cfg(proxy_cfg: dict[str, Any]) -> dict[str, Any]:
    reverse = proxy_cfg.get("reverse")
    if isinstance(reverse, dict):
        return reverse
    if proxy_cfg.get("upstreams") or proxy_cfg.get("endpoints"):
        return proxy_cfg
    raise ValueError("network.proxy.reverse must be configured")


def forward_proxy_cfg(proxy_cfg: dict[str, Any]) -> dict[str, Any]:
    forward = proxy_cfg.get("forward")
    if isinstance(forward, dict):
        return forward
    return {"enabled": False, "port": DEFAULT_FORWARD_PORT, "host": "127.0.0.1"}


def debug_cfg(proxy_cfg: dict[str, Any]) -> dict[str, Any]:
    debug = proxy_cfg.get("debug")
    return debug if isinstance(debug, dict) else {}


def resolve_reverse_port(config: dict[str, Any], cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    proxy_cfg = config.get("network", {}).get("proxy", {})
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    return int(reverse_cfg.get("port", DEFAULT_PORT))


def resolve_forward_port(config: dict[str, Any], cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    proxy_cfg = config.get("network", {}).get("proxy", {})
    forward_cfg = forward_proxy_cfg(proxy_cfg)
    return int(forward_cfg.get("port", DEFAULT_FORWARD_PORT))


def forward_enabled(config: dict[str, Any], cli_no_forward: bool) -> bool:
    if cli_no_forward:
        return False
    proxy_cfg = config.get("network", {}).get("proxy", {})
    forward_cfg = forward_proxy_cfg(proxy_cfg)
    return bool(forward_cfg.get("enabled", True))


def forward_bind_host(config: dict[str, Any]) -> str:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    forward_cfg = forward_proxy_cfg(proxy_cfg)
    return str(forward_cfg.get("host", "127.0.0.1"))


def _resolve_config_path(rel: str) -> Path:
    base = Path(__file__).resolve().parent
    path = Path(rel)
    if path.is_absolute():
        return path
    if rel.startswith("src/"):
        return base.parent / rel
    return base / rel


def mitm_ca_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    forward_cfg = forward_proxy_cfg(proxy_cfg)
    mitm = forward_cfg.get("mitm") if isinstance(forward_cfg.get("mitm"), dict) else {}
    ca_cert = _resolve_config_path(str(mitm.get("ca_cert", "src/crt/mitm-ca.pem")))
    ca_key = _resolve_config_path(str(mitm.get("ca_key", "src/crt/mitm-ca-key.pem")))
    return ca_cert, ca_key


def resolve_forward_debug_log(config: dict[str, Any]) -> Path:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    dbg = debug_cfg(proxy_cfg)
    return Path(str(dbg.get("forward_log", "forward.log")))


def debug_max_body_bytes(config: dict[str, Any]) -> int:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    dbg = debug_cfg(proxy_cfg)
    return int(dbg.get("max_body_bytes", 1048576))


def debug_log_response_body(config: dict[str, Any]) -> bool:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    dbg = debug_cfg(proxy_cfg)
    return bool(dbg.get("log_response_body", True))


def proxy_http2_settings(config: dict[str, Any]) -> dict[str, Any]:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    http2_cfg = reverse_cfg.get("http2")
    if http2_cfg is None:
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


def filter_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP
    }


def filter_header_dict(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def header_content_encoding(headers: dict[str, str]) -> str | None:
    return headers.get("content-encoding") or headers.get("connect-content-encoding")


def _decompress_body(body: bytes, content_encoding: str | None) -> bytes:
    if not body:
        return body
    if content_encoding:
        for enc in (part.strip() for part in content_encoding.split(",")):
            lower = enc.lower()
            if lower in {"identity", ""}:
                continue
            if lower in {"gzip", "x-gzip"}:
                return gzip.decompress(body)
            if lower == "deflate":
                return zlib.decompress(body)
            if lower == "br":
                try:
                    import brotli
                except ImportError:
                    return body
                return brotli.decompress(body)
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
    from connect_envelope import decode_connect_payload

    decoded = decode_connect_payload(truncated, content_encoding=content_encoding)
    if decoded is truncated and content_encoding:
        decoded = _decompress_body(decoded, content_encoding)
    elif decoded is truncated:
        decoded = _decompress_body(decoded, content_encoding)
    if content_type and "json" in content_type.lower():
        try:
            value = json.loads(decoded)
            if max_bytes is not None and original_len > max_bytes:
                return {
                    "_json": value,
                    "_truncated": True,
                    "_original_bytes": original_len,
                }
            return value
        except json.JSONDecodeError:
            pass
    if content_type and any(
        token in content_type.lower() for token in ("proto", "protobuf", "connect")
    ):
        text = _extract_printable_text(decoded)
        if text.strip():
            if max_bytes is not None and original_len > max_bytes:
                return {"_text": text, "_truncated": True, "_original_bytes": original_len}
            return text
    text = _bytes_to_log_text(decoded, content_type)
    if max_bytes is not None and original_len > max_bytes:
        return {"_text": text, "_truncated": True, "_original_bytes": original_len}
    return text


def optional_body_log_field(
    body: bytes,
    content_type: str | None,
    *,
    content_encoding: str | None = None,
    max_bytes: int | None = None,
    field: str = "request_body",
) -> dict[str, Any]:
    value = body_for_snapshot(
        body,
        content_type,
        content_encoding=content_encoding,
        max_bytes=max_bytes,
    )
    if value is None:
        return {}
    return {field: value}


def append_debug_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    timestamp = datetime.now(UTC).isoformat()
    block = f"--- {timestamp} ---\n{json.dumps(snapshot, indent=2)}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


async def save_debug_snapshot(path: Path, snapshot: dict[str, Any]) -> Path:
    await asyncio.to_thread(append_debug_snapshot, path, snapshot)
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
        content: bytes | AsyncIterator[bytes] = iter_request_body(request)
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

    await serve(app, cfg)


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


async def run_servers(
    *,
    config: dict[str, Any],
    reverse_port: int,
    forward_port: int,
    forward_host: str,
    run_forward: bool,
    debug: bool,
    debug_strict: bool,
    http2_upstream: bool,
    http2_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    from proxy_forward import run_forward_mitm_proxy
    from proxy_reverse import serve_reverse_async

    forward_task = None
    if run_forward:
        ca_cert, ca_key = mitm_ca_paths(config)
        forward_task = asyncio.create_task(
            run_forward_mitm_proxy(
                host=forward_host,
                port=forward_port,
                ca_cert_path=ca_cert,
                ca_key_path=ca_key,
                debug=debug,
                debug_log=resolve_forward_debug_log(config),
                max_body_bytes=debug_max_body_bytes(config),
                log_response_body=debug_log_response_body(config),
            ),
            name="forward-mitm",
        )
        logger.info("forward MITM proxy listening on %s:%s", forward_host, forward_port)

    reverse_task = asyncio.create_task(
        serve_reverse_async(
            config,
            host="0.0.0.0",
            port=reverse_port,
            debug=debug,
            debug_strict=debug_strict,
            http2_upstream=http2_upstream,
            http2_serve=http2_serve,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        ),
        name="reverse-proxy",
    )
    logger.info("reverse proxy listening on 0.0.0.0:%s", reverse_port)

    tasks = [reverse_task]
    if forward_task is not None:
        tasks.append(forward_task)
    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse + MITM forward HTTP proxy")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run reverse and forward proxies")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Reverse listen port (default from config, else {DEFAULT_PORT})",
    )
    serve_parser.add_argument(
        "--forward-port",
        type=int,
        default=None,
        help=f"Forward MITM port (default from config, else {DEFAULT_FORWARD_PORT})",
    )
    serve_parser.add_argument(
        "--no-forward",
        action="store_true",
        help="Disable forward MITM proxy",
    )
    serve_parser.add_argument("--config", type=Path, default=None)
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        help="Reverse: dry-run to {endpoint}.log; forward: append decrypted bodies to forward.log",
    )
    serve_parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug on reverse, return 502 when pruning did not apply",
    )
    serve_parser.add_argument("--http2-upstream", action=argparse.BooleanOptionalAction, default=None)
    serve_parser.add_argument("--http2-serve", action=argparse.BooleanOptionalAction, default=None)
    serve_parser.add_argument("--ssl-keyfile", type=Path, default=None)
    serve_parser.add_argument("--ssl-certfile", type=Path, default=None)

    stats_parser = subparsers.add_parser("stats", help="Query persisted proxy stats")
    stats_sub = stats_parser.add_subparsers(dest="stats_command", required=True)
    stats_totals = stats_sub.add_parser("totals")
    stats_totals.add_argument("--period", choices=["day", "week", "month", "all"], default="all")
    stats_totals.add_argument("--config", type=Path, default=None)
    stats_summary = stats_sub.add_parser("summary")
    stats_summary.add_argument("--period", choices=["day", "week", "month", "all"], default="day")
    stats_summary.add_argument("--config", type=Path, default=None)
    stats_events = stats_sub.add_parser("events")
    stats_events.add_argument("--limit", type=int, default=20)
    stats_events.add_argument("--json", action="store_true")
    stats_events.add_argument("--config", type=Path, default=None)

    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--forward-port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-forward", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-strict", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "stats":
        config = load_proxy_config(getattr(args, "config", None))
        _run_stats_cli(args, config)
        return

    if args.command is None:
        args.command = "serve"
        for attr, default in (
            ("debug", False),
            ("debug_strict", False),
            ("no_forward", False),
        ):
            if not hasattr(args, attr):
                setattr(args, attr, default)

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

    http2_settings = proxy_http2_settings(config)
    http2_upstream = (
        args.http2_upstream if args.http2_upstream is not None else http2_settings["http2_upstream"]
    )
    http2_serve = args.http2_serve if args.http2_serve is not None else http2_settings["http2_serve"]
    ssl_keyfile = (
        str(args.ssl_keyfile) if args.ssl_keyfile is not None else http2_settings["ssl_keyfile"]
    )
    ssl_certfile = (
        str(args.ssl_certfile) if args.ssl_certfile is not None else http2_settings["ssl_certfile"]
    )

    asyncio.run(
        run_servers(
            config=config,
            reverse_port=resolve_reverse_port(config, args.port),
            forward_port=resolve_forward_port(config, getattr(args, "forward_port", None)),
            forward_host=forward_bind_host(config),
            run_forward=forward_enabled(config, getattr(args, "no_forward", False)),
            debug=args.debug,
            debug_strict=args.debug_strict,
            http2_upstream=http2_upstream,
            http2_serve=http2_serve,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        ),
    )


if __name__ == "__main__":
    main()
