"""Generic MITM forward HTTP proxy on a dedicated port."""

from __future__ import annotations

import asyncio
import logging
import select
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from connect_envelope import (
    decompress_frame_payload,
    parse_frames,
    should_parse_connect_frames,
)
from proxy import body_for_snapshot, header_content_encoding, optional_body_log_field
from proxy_forward_mitm import (
    MitmAuthority,
    build_http_request,
    build_http_response,
    filter_header_dict,
    is_benign_client_disconnect,
    parse_host_port,
    parse_request_line,
    read_http_body,
    read_http_headers,
    read_http_message,
    read_proxy_request,
    request_path_from_target,
    status_reason,
    upstream_connect,
)

logger = logging.getLogger(__name__)


@dataclass
class ForwardExchange:
    host: str
    method: str
    path: str
    query: str
    request_headers: dict[str, str]
    request_body: bytes

    @property
    def content_type(self) -> str | None:
        return self.request_headers.get("content-type")

    @property
    def content_encoding(self) -> str | None:
        return header_content_encoding(self.request_headers)


async def transform_forward_request(exchange: ForwardExchange) -> ForwardExchange:
    return exchange


async def transform_forward_response(
    exchange: ForwardExchange,
    status: int,
    response_headers: dict[str, str],
    response_body: bytes,
) -> tuple[int, dict[str, str], bytes]:
    return status, response_headers, response_body


@dataclass
class ForwardDebugConfig:
    enabled: bool
    log_path: Path
    max_body_bytes: int
    log_response_body: bool


def _run_transform_request(exchange: ForwardExchange) -> ForwardExchange:
    return asyncio.run(transform_forward_request(exchange))


def _run_transform_response(
    exchange: ForwardExchange,
    status: int,
    headers: dict[str, str],
    body: bytes,
) -> tuple[int, dict[str, str], bytes]:
    return asyncio.run(
        transform_forward_response(exchange, status, headers, body),
    )


def _append_debug_sync(debug: ForwardDebugConfig, snapshot: dict[str, Any]) -> None:
    if not debug.enabled:
        return
    from proxy import append_debug_snapshot

    append_debug_snapshot(debug.log_path, snapshot)


def _log_value_empty(value: Any) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if value == {}:
        return True
    if value == []:
        return True
    return False


def _snapshot_has_payload(snapshot: dict[str, Any]) -> bool:
    for field in ("request_body", "transformed_request_body", "frame_body"):
        if field in snapshot and not _log_value_empty(snapshot[field]):
            return True
    return False


def _append_request_debug_sync(
    debug: ForwardDebugConfig,
    snapshot: dict[str, Any],
) -> None:
    if not _snapshot_has_payload(snapshot):
        return
    _append_debug_sync(debug, snapshot)


def log_connect_frames(
    debug: ForwardDebugConfig,
    *,
    host: str,
    path: str,
    direction: str,
    buffer: bytearray,
    content_type: str | None,
    content_encoding: str | None = None,
    protocol: str = "h2",
    stream_id: int | None = None,
) -> None:
    if not debug.enabled:
        return
    for frame in parse_frames(buffer):
        payload = decompress_frame_payload(frame, content_encoding=content_encoding)
        effective_type = content_type
        if host == "api2.cursor.sh" and not effective_type:
            effective_type = "application/proto"
        frame_body = body_for_snapshot(
            payload,
            effective_type,
            content_encoding=content_encoding,
            max_bytes=debug.max_body_bytes,
        )
        if frame_body is None or frame_body == {}:
            continue
        snapshot: dict[str, Any] = {
            "mode": "forward",
            "type": "mitm_connect_frame",
            "protocol": protocol,
            "direction": direction,
            "host": host,
            "path": path,
            "frame_compressed": frame.compressed,
            "frame_body": frame_body,
        }
        if stream_id is not None:
            snapshot["stream_id"] = stream_id
        _append_request_debug_sync(debug, snapshot)


def _append_response_debug_sync(
    debug: ForwardDebugConfig,
    snapshot: dict[str, Any],
    *,
    body: bytes,
) -> None:
    if not debug.log_response_body or not body:
        return
    _append_debug_sync(debug, snapshot)


def _mitm_http11_connect_duplex(
    client_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: ForwardDebugConfig,
    *,
    method: str,
    target: str,
    req_headers: dict[str, str],
    initial: bytes,
) -> None:
    req_path = request_path_from_target(target)
    query = ""
    if "?" in req_path:
        req_path, query = req_path.split("?", 1)
    upstream_path = req_path
    if query:
        upstream_path = f"{upstream_path}?{query}"

    filtered = filter_header_dict(req_headers)
    upstream = upstream_connect(host, port)
    upstream.sendall(
        build_http_request(
            method=method,
            path=upstream_path,
            headers=filtered,
            body=initial,
            host=host,
        ),
    )

    client_buf = bytearray(initial)
    server_buf = bytearray()
    req_content_type = filtered.get("content-type")
    req_encoding = header_content_encoding(filtered)
    resp_headers: dict[str, str] = {}
    resp_parsed = False
    resp_header_buf = bytearray()
    client_open = True
    upstream_open = True

    log_connect_frames(
        debug,
        host=host,
        path=req_path,
        direction="client",
        buffer=client_buf,
        content_type=req_content_type,
        content_encoding=req_encoding,
        protocol="http/1.1",
    )

    while client_open or upstream_open:
        read_list: list[socket.socket] = []
        if client_open:
            read_list.append(client_ssl)
        if upstream_open:
            read_list.append(upstream)
        if not read_list:
            break
        readable, _, _ = select.select(read_list, [], [], 120.0)
        if not readable:
            break

        if client_ssl in readable:
            try:
                chunk = client_ssl.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                client_open = False
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            else:
                client_buf.extend(chunk)
                log_connect_frames(
                    debug,
                    host=host,
                    path=req_path,
                    direction="client",
                    buffer=client_buf,
                    content_type=req_content_type,
                    content_encoding=req_encoding,
                    protocol="http/1.1",
                )
                upstream.sendall(chunk)

        if upstream in readable:
            try:
                chunk = upstream.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                upstream_open = False
            elif not resp_parsed:
                resp_header_buf.extend(chunk)
                if b"\r\n\r\n" not in resp_header_buf:
                    continue
                header_block, body_start = bytes(resp_header_buf).split(b"\r\n\r\n", 1)
                resp_line, resp_headers = _parse_upstream_response_headers(header_block)
                client_ssl.sendall(resp_header_buf)
                resp_parsed = True
                resp_header_buf.clear()
                if body_start:
                    server_buf.extend(body_start)
                    log_connect_frames(
                        debug,
                        host=host,
                        path=req_path,
                        direction="server",
                        buffer=server_buf,
                        content_type=resp_headers.get("content-type"),
                        content_encoding=header_content_encoding(resp_headers),
                        protocol="http/1.1",
                    )
            else:
                server_buf.extend(chunk)
                log_connect_frames(
                    debug,
                    host=host,
                    path=req_path,
                    direction="server",
                    buffer=server_buf,
                    content_type=resp_headers.get("content-type"),
                    content_encoding=header_content_encoding(resp_headers),
                    protocol="http/1.1",
                )
                client_ssl.sendall(chunk)

    upstream.close()


def _parse_upstream_response_headers(header_block: bytes) -> tuple[str, dict[str, str]]:
    from proxy_forward_mitm import _parse_headers

    return _parse_headers(header_block)


def _mitm_http11_exchange(
    client_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: ForwardDebugConfig,
) -> bool:
    """Handle one HTTP/1.1 request/response. Returns False when the connection should close."""
    start_line, req_headers, rest = read_http_headers(client_ssl)
    method, target, _version = parse_request_line(start_line)
    path = request_path_from_target(target)
    req_path = path.split("?", 1)[0]
    if should_parse_connect_frames(host, req_headers, req_path):
        _mitm_http11_connect_duplex(
            client_ssl,
            host,
            port,
            debug,
            method=method,
            target=target,
            req_headers=req_headers,
            initial=rest,
        )
        return False

    req_body = read_http_body(client_ssl, req_headers, initial=rest)
    if method.upper() in {"POST", "PUT", "PATCH"} and req_body == b"":
        if "content-length" not in req_headers and "chunked" not in req_headers.get(
            "transfer-encoding", "",
        ).lower():
            client_ssl.settimeout(0.5)
            try:
                while True:
                    chunk = client_ssl.recv(65536)
                    if not chunk:
                        break
                    req_body += chunk
            except (TimeoutError, OSError):
                pass
            finally:
                client_ssl.settimeout(120)

    path = request_path_from_target(target)
    query = ""
    req_path = path
    if "?" in path:
        req_path, query = path.split("?", 1)

    exchange = ForwardExchange(
        host=host,
        method=method,
        path=req_path,
        query=query,
        request_headers=filter_header_dict(req_headers),
        request_body=req_body,
    )
    original_body = exchange.request_body
    exchange = _run_transform_request(exchange)

    _append_request_debug_sync(
        debug,
        {
            "mode": "forward",
            "type": "mitm",
            "protocol": "http/1.1",
            "host": host,
            "port": port,
            "method": exchange.method,
            "path": exchange.path,
            "query": exchange.query or None,
            "request_headers": exchange.request_headers,
            **optional_body_log_field(
                original_body,
                exchange.content_type,
                content_encoding=exchange.content_encoding,
                max_bytes=debug.max_body_bytes,
            ),
            **(
                optional_body_log_field(
                    exchange.request_body,
                    exchange.content_type,
                    content_encoding=exchange.content_encoding,
                    max_bytes=debug.max_body_bytes,
                    field="transformed_request_body",
                )
                if exchange.request_body != original_body
                else {}
            ),
        },
    )

    upstream_path = exchange.path
    if exchange.query:
        upstream_path = f"{upstream_path}?{exchange.query}"
    upstream_request = build_http_request(
        method=exchange.method,
        path=upstream_path,
        headers=exchange.request_headers,
        body=exchange.request_body,
        host=host,
    )

    try:
        upstream = upstream_connect(host, port)
    except OSError as exc:
        logger.warning("upstream connect failed host=%s: %s", host, exc)
        client_ssl.sendall(
            build_http_response(
                status=502,
                reason=status_reason(502),
                headers={"content-type": "text/plain"},
                body=str(exc).encode(),
            ),
        )
        return False

    upstream.sendall(upstream_request)
    resp_line, resp_headers, resp_body = read_http_message(upstream)
    upstream.close()

    status_parts = resp_line.split(" ", 2)
    status = int(status_parts[1]) if len(status_parts) >= 2 else 502
    status, resp_headers, resp_body = _run_transform_response(
        exchange,
        status,
        filter_header_dict(resp_headers),
        resp_body,
    )

    _append_response_debug_sync(
        debug,
        {
            "mode": "forward",
            "type": "mitm_response",
            "protocol": "http/1.1",
            "host": host,
            "status": status,
            "response_headers": resp_headers,
            **optional_body_log_field(
                resp_body,
                resp_headers.get("content-type"),
                content_encoding=header_content_encoding(resp_headers),
                max_bytes=debug.max_body_bytes,
                field="response_body",
            ),
        },
        body=resp_body,
    )

    keep_alive = "close" not in req_headers.get("connection", "").lower()
    resp_headers_out = dict(resp_headers)
    resp_headers_out["connection"] = "keep-alive" if keep_alive else "close"
    client_ssl.sendall(
        build_http_response(
            status=status,
            reason=status_reason(status),
            headers=resp_headers_out,
            body=resp_body,
        ),
    )
    return keep_alive


def _mitm_https_relay(
    client_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: ForwardDebugConfig,
) -> None:
    alpn = client_ssl.selected_alpn_protocol()
    if alpn == "h2":
        from proxy_forward_h2 import mitm_h2_relay

        mitm_h2_relay(client_ssl, host, port, debug)
        return

    while True:
        try:
            if not _mitm_http11_exchange(client_ssl, host, port, debug):
                break
        except ConnectionError:
            break
        except OSError as exc:
            if is_benign_client_disconnect(exc):
                break
            raise
    try:
        client_ssl.close()
    except OSError:
        pass


def _mitm_https_relay_after_connect(
    client_sock: socket.socket,
    host: str,
    port: int,
    mitm: MitmAuthority,
    debug: ForwardDebugConfig,
) -> None:
    server_ctx = mitm.server_ssl_context(host)
    try:
        client_ssl = server_ctx.wrap_socket(client_sock, server_side=True)
    except ssl.SSLError as exc:
        logger.warning(
            "MITM TLS handshake failed for %s:%s (%s). "
            "The client must trust src/crt/mitm-ca.pem "
            "(macOS Keychain or NODE_EXTRA_CA_CERTS for Cursor/Electron).",
            host,
            port,
            exc,
        )
        raise

    _mitm_https_relay(client_ssl, host, port, debug)


def _log_client_error(exc: BaseException, *, host: str | None = None, tunnel: bool = False) -> None:
    target = f"{host}:443" if host else "client"
    if is_benign_client_disconnect(exc):
        logger.debug("forward proxy %s disconnected (%s)", target, exc)
        return
    if tunnel:
        logger.warning("forward proxy tunnel error for %s: %s", target, exc)
    else:
        logger.warning("forward proxy client error: %s", exc)


def _safe_send_502(client_sock: socket.socket) -> None:
    try:
        client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
    except OSError:
        pass


def _safe_close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


def _forward_http_relay(
    client_sock: socket.socket,
    method: str,
    target: str,
    req_headers: dict[str, str],
    req_body: bytes,
    mitm: MitmAuthority,
    debug: ForwardDebugConfig,
) -> None:
    if target.startswith(("http://", "https://")):
        host, port = parse_host_port(target)
        path = request_path_from_target(target)
    else:
        host_header = req_headers.get("host", "")
        host, port = parse_host_port(host_header, 80)
        path = request_path_from_target(target)

    query = ""
    req_path = path
    if "?" in path:
        req_path, query = path.split("?", 1)

    exchange = ForwardExchange(
        host=host,
        method=method,
        path=req_path,
        query=query,
        request_headers=filter_header_dict(req_headers),
        request_body=req_body,
    )
    original_body = exchange.request_body
    exchange = _run_transform_request(exchange)

    _append_request_debug_sync(
        debug,
        {
            "mode": "forward",
            "type": "http",
            "target": target,
            **optional_body_log_field(
                original_body,
                exchange.content_type,
                content_encoding=exchange.content_encoding,
                max_bytes=debug.max_body_bytes,
            ),
        },
    )

    upstream_path = exchange.path
    if exchange.query:
        upstream_path = f"{upstream_path}?{exchange.query}"
    upstream_request = build_http_request(
        method=exchange.method,
        path=upstream_path,
        headers=exchange.request_headers,
        body=exchange.request_body,
        host=host,
    )

    try:
        if port == 443 or target.startswith("https://"):
            upstream = upstream_connect(host, port)
        else:
            upstream = socket.create_connection((host, port), timeout=30)
    except OSError as exc:
        client_sock.sendall(
            build_http_response(
                status=502,
                reason=status_reason(502),
                headers={"content-type": "text/plain"},
                body=str(exc).encode(),
            ),
        )
        client_sock.close()
        return

    upstream.sendall(upstream_request)
    resp_line, resp_headers, resp_body = read_http_message(upstream)
    upstream.close()

    status_parts = resp_line.split(" ", 2)
    status = int(status_parts[1]) if len(status_parts) >= 2 else 502
    status, resp_headers, resp_body = _run_transform_response(
        exchange,
        status,
        filter_header_dict(resp_headers),
        resp_body,
    )

    _append_response_debug_sync(
        debug,
        {
            "mode": "forward",
            "type": "http_response",
            "target": target,
            "status": status,
            **optional_body_log_field(
                resp_body,
                resp_headers.get("content-type"),
                content_encoding=header_content_encoding(resp_headers),
                max_bytes=debug.max_body_bytes,
                field="response_body",
            ),
        },
        body=resp_body,
    )

    client_sock.sendall(
        build_http_response(
            status=status,
            reason=status_reason(status),
            headers=resp_headers,
            body=resp_body,
        ),
    )
    client_sock.close()


def handle_client_sync(
    client_sock: socket.socket,
    *,
    mitm: MitmAuthority,
    debug: ForwardDebugConfig,
) -> None:
    tunnel_established = False
    connect_host: str | None = None
    try:
        client_sock.settimeout(120)
        method, target, req_headers, req_body = read_proxy_request(client_sock)
        if method.upper() == "CONNECT":
            connect_host, port = parse_host_port(target)
            if connect_host.endswith("cursor.sh"):
                logger.info("MITM CONNECT %s:%s", connect_host, port)
            client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            tunnel_established = True
            _mitm_https_relay_after_connect(client_sock, connect_host, port, mitm, debug)
            return
        _forward_http_relay(client_sock, method, target, req_headers, req_body, mitm, debug)
    except ssl.SSLError:
        pass
    except Exception as exc:
        _log_client_error(
            exc,
            host=connect_host,
            tunnel=tunnel_established,
        )
        if not tunnel_established:
            _safe_send_502(client_sock)
    finally:
        _safe_close(client_sock)


async def run_forward_mitm_proxy(
    *,
    host: str,
    port: int,
    ca_cert_path: Path,
    ca_key_path: Path,
    debug: bool,
    debug_log: Path,
    max_body_bytes: int,
    log_response_body: bool,
) -> None:
    mitm = MitmAuthority(ca_cert_path, ca_key_path)
    debug_cfg = ForwardDebugConfig(
        enabled=debug,
        log_path=debug_log,
        max_body_bytes=max_body_bytes,
        log_response_body=log_response_body,
    )

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(256)
    server.setblocking(False)
    loop = asyncio.get_running_loop()

    async def _accept_loop() -> None:
        while True:
            client_sock, _addr = await loop.sock_accept(server)
            task = asyncio.create_task(
                asyncio.to_thread(
                    handle_client_sync,
                    client_sock,
                    mitm=mitm,
                    debug=debug_cfg,
                ),
            )
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    await _accept_loop()
