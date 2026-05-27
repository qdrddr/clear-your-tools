"""HTTP/2 MITM relay for the forward proxy."""

from __future__ import annotations

import logging
import select
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import (
    ConnectionTerminated,
    DataReceived,
    RequestReceived,
    ResponseReceived,
    StreamEnded,
    StreamReset,
)

from connect_envelope import should_parse_connect_frames
from proxy import header_content_encoding, optional_body_log_field
from proxy_forward_mitm import (
    HOP_BY_HOP_SKIP,
    build_http_request,
    filter_header_dict,
    is_benign_client_disconnect,
    read_http_message,
    upstream_connect,
    upstream_connect_with_alpn,
)

logger = logging.getLogger(__name__)
RELAY_TIMEOUT = 120.0


@dataclass
class _H2StreamState:
    client_stream_id: int
    upstream_stream_id: int | None = None
    method: str = ""
    path: str = "/"
    query: str = ""
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: bytearray = field(default_factory=bytearray)
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: bytearray = field(default_factory=bytearray)
    response_status: int = 200
    request_complete: bool = False
    response_complete: bool = False
    request_logged: bool = False
    response_logged: bool = False
    upstream_opened: bool = False
    stream_metadata_logged: bool = False
    connect_streaming: bool = False
    connect_client_buffer: bytearray = field(default_factory=bytearray)
    connect_server_buffer: bytearray = field(default_factory=bytearray)


def _flush_h2(sock: ssl.SSLSocket, conn: H2Connection) -> None:
    data = conn.data_to_send()
    if data:
        sock.sendall(data)


def _recv_ssl(sock: ssl.SSLSocket) -> bytes | None:
    try:
        return sock.recv(65536)
    except OSError as exc:
        if is_benign_client_disconnect(exc):
            return None
        raise


def _parse_h2_request(headers: list[tuple[bytes, bytes]]) -> tuple[str, str, str, dict[str, str]]:
    pseudo: dict[str, str] = {}
    regular: dict[str, str] = {}
    for name, value in headers:
        key = name.decode("latin-1")
        val = value.decode("latin-1")
        if key.startswith(":"):
            pseudo[key] = val
        else:
            lower = key.lower()
            if lower in regular:
                regular[lower] = f"{regular[lower]}, {val}"
            else:
                regular[lower] = val
    method = pseudo.get(":method", "GET")
    path = pseudo.get(":path", "/")
    query = ""
    req_path = path
    if "?" in path:
        req_path, query = path.split("?", 1)
    return method, req_path, query, regular


def _build_upstream_request_headers(
    method: str,
    path: str,
    host: str,
    headers: dict[str, str],
) -> list[tuple[bytes, bytes]]:
    authority = headers.get("host") or host
    block: list[tuple[bytes, bytes]] = [
        (b":method", method.encode("latin-1")),
        (b":path", path.encode("latin-1")),
        (b":scheme", b"https"),
        (b":authority", authority.encode("latin-1")),
    ]
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_SKIP or lower.startswith(":"):
            continue
        block.append((lower.encode("latin-1"), value.encode("latin-1")))
    return block


def _parse_h2_response(headers: list[tuple[bytes, bytes]]) -> tuple[int, dict[str, str]]:
    status = 502
    regular: dict[str, str] = {}
    for name, value in headers:
        key = name.decode("latin-1")
        val = value.decode("latin-1")
        if key == ":status":
            try:
                status = int(val)
            except ValueError:
                status = 502
        elif not key.startswith(":"):
            lower = key.lower()
            if lower in regular:
                regular[lower] = f"{regular[lower]}, {val}"
            else:
                regular[lower] = val
    return status, regular


def _build_client_response_headers(
    status: int,
    headers: dict[str, str],
) -> list[tuple[bytes, bytes]]:
    block: list[tuple[bytes, bytes]] = [(b":status", str(status).encode("latin-1"))]
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_SKIP or lower.startswith(":"):
            continue
        block.append((lower.encode("latin-1"), value.encode("latin-1")))
    return block


def _init_stream_state(state: _H2StreamState, *, host: str) -> None:
    if state.stream_metadata_logged:
        return
    state.stream_metadata_logged = True
    state.connect_streaming = should_parse_connect_frames(
        host, state.request_headers, state.path
    )
    if host == "api2.cursor.sh":
        logger.info(
            "H2 api2 stream %s %s connect=%s ctype=%s",
            state.method,
            state.path,
            state.connect_streaming,
            state.request_headers.get("content-type"),
        )


def _log_connect_frames(
    *,
    host: str,
    state: _H2StreamState,
    debug: Any,
    direction: str,
    buffer: bytearray,
) -> None:
    from proxy_forward import log_connect_frames

    headers = state.request_headers if direction == "client" else state.response_headers
    log_connect_frames(
        debug,
        host=host,
        path=state.path,
        direction=direction,
        buffer=buffer,
        content_type=headers.get("content-type"),
        content_encoding=header_content_encoding(headers),
        protocol="h2",
        stream_id=state.client_stream_id,
    )


def _log_request(*, host: str, port: int, state: _H2StreamState, debug: Any) -> None:
    from proxy_forward import (
        ForwardExchange,
        _append_request_debug_sync,
        _run_transform_request,
    )

    if state.request_logged or state.connect_streaming:
        return
    state.request_logged = True
    exchange = ForwardExchange(
        host=host,
        method=state.method,
        path=state.path,
        query=state.query,
        request_headers=filter_header_dict(state.request_headers),
        request_body=bytes(state.request_body),
    )
    original_body = exchange.request_body
    exchange = _run_transform_request(exchange)
    if original_body:
        _append_request_debug_sync(
            debug,
            {
                "mode": "forward",
                "type": "mitm",
                "protocol": "h2",
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


def _log_response(*, host: str, state: _H2StreamState, debug: Any) -> None:
    from proxy_forward import (
        ForwardExchange,
        _append_response_debug_sync,
        _run_transform_response,
    )

    if state.response_logged or not debug.log_response_body:
        return
    if not state.response_body:
        state.response_logged = True
        return
    exchange = ForwardExchange(
        host=host,
        method=state.method,
        path=state.path,
        query=state.query,
        request_headers=filter_header_dict(state.request_headers),
        request_body=bytes(state.request_body),
    )
    headers = filter_header_dict(state.response_headers)
    status, headers, body = _run_transform_response(
        exchange,
        state.response_status,
        headers,
        bytes(state.response_body),
    )
    _append_response_debug_sync(
        debug,
        {
            "mode": "forward",
            "type": "mitm_response",
            "protocol": "h2",
            "host": host,
            "status": status,
            "response_headers": headers,
            **optional_body_log_field(
                body,
                headers.get("content-type"),
                content_encoding=header_content_encoding(headers),
                max_bytes=debug.max_body_bytes,
                field="response_body",
            ),
        },
        body=body,
    )
    state.response_logged = True


def _ensure_upstream_stream(
    *,
    upstream_conn: H2Connection,
    upstream_ssl: ssl.SSLSocket,
    state: _H2StreamState,
    host: str,
    end_stream: bool,
) -> None:
    if state.upstream_opened:
        if end_stream and state.upstream_stream_id is not None:
            upstream_conn.end_stream(state.upstream_stream_id)
            _flush_h2(upstream_ssl, upstream_conn)
        return
    upstream_path = state.path
    if state.query:
        upstream_path = f"{upstream_path}?{state.query}"
    upstream_stream_id = upstream_conn.get_next_available_stream_id()
    state.upstream_stream_id = upstream_stream_id
    state.upstream_opened = True
    upstream_headers = _build_upstream_request_headers(
        state.method,
        upstream_path,
        host,
        state.request_headers,
    )
    upstream_conn.send_headers(
        upstream_stream_id,
        upstream_headers,
        end_stream=end_stream and not state.request_body,
    )
    if state.request_body:
        upstream_conn.send_data(
            upstream_stream_id,
            bytes(state.request_body),
            end_stream=end_stream,
        )
    _flush_h2(upstream_ssl, upstream_conn)


def _complete_request(*, state: _H2StreamState, host: str, port: int, debug: Any) -> None:
    if state.request_complete:
        return
    state.request_complete = True
    if debug.enabled:
        _log_request(host=host, port=port, state=state, debug=debug)


def _complete_response(*, state: _H2StreamState, host: str, debug: Any) -> None:
    if state.response_complete:
        return
    state.response_complete = True
    if debug.enabled:
        _log_response(host=host, state=state, debug=debug)


def _relay_h2_over_h2(
    client_ssl: ssl.SSLSocket,
    upstream_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: Any,
) -> None:
    client_conn = H2Connection(config=H2Configuration(client_side=False))
    client_conn.initiate_connection()
    _flush_h2(client_ssl, client_conn)

    upstream_conn = H2Connection(config=H2Configuration(client_side=True))
    upstream_conn.initiate_connection()
    _flush_h2(upstream_ssl, upstream_conn)

    streams: dict[int, _H2StreamState] = {}
    upstream_to_client: dict[int, int] = {}
    client_open = True
    upstream_open = True

    while client_open or upstream_open:
        read_list: list[socket.socket] = []
        if client_open:
            read_list.append(client_ssl)
        if upstream_open:
            read_list.append(upstream_ssl)
        if not read_list:
            break
        readable, _, _ = select.select(read_list, [], [], RELAY_TIMEOUT)
        if not readable:
            break

        if client_ssl in readable:
            chunk = _recv_ssl(client_ssl)
            if not chunk:
                client_open = False
                upstream_conn.close_connection()
                _flush_h2(upstream_ssl, upstream_conn)
            else:
                for event in client_conn.receive_data(chunk):
                    if isinstance(event, RequestReceived):
                        method, path, query, headers = _parse_h2_request(event.headers)
                        state = _H2StreamState(
                            client_stream_id=event.stream_id,
                            method=method,
                            path=path,
                            query=query,
                            request_headers=headers,
                        )
                        streams[event.stream_id] = state
                        _init_stream_state(state, host=host)
                        end_stream = event.stream_ended is not None
                        _ensure_upstream_stream(
                            upstream_conn=upstream_conn,
                            upstream_ssl=upstream_ssl,
                            state=state,
                            host=host,
                            end_stream=end_stream,
                        )
                        if state.upstream_stream_id is not None:
                            upstream_to_client[state.upstream_stream_id] = event.stream_id
                        if end_stream:
                            _complete_request(state=state, host=host, port=port, debug=debug)
                    elif isinstance(event, DataReceived):
                        state = streams.get(event.stream_id)
                        if state is None:
                            continue
                        state.request_body.extend(event.data)
                        if state.connect_streaming:
                            state.connect_client_buffer.extend(event.data)
                            _log_connect_frames(
                                host=host,
                                state=state,
                                debug=debug,
                                direction="client",
                                buffer=state.connect_client_buffer,
                            )
                        end_stream = event.stream_ended is not None
                        if not state.upstream_opened:
                            _ensure_upstream_stream(
                                upstream_conn=upstream_conn,
                                upstream_ssl=upstream_ssl,
                                state=state,
                                host=host,
                                end_stream=end_stream,
                            )
                            if state.upstream_stream_id is not None:
                                upstream_to_client[state.upstream_stream_id] = event.stream_id
                        elif state.upstream_stream_id is not None:
                            upstream_conn.send_data(
                                state.upstream_stream_id,
                                event.data,
                                end_stream=end_stream,
                            )
                            _flush_h2(upstream_ssl, upstream_conn)
                        if end_stream:
                            _complete_request(state=state, host=host, port=port, debug=debug)
                    elif isinstance(event, StreamEnded):
                        state = streams.get(event.stream_id)
                        if state is not None:
                            _complete_request(state=state, host=host, port=port, debug=debug)
                    elif isinstance(event, StreamReset):
                        streams.pop(event.stream_id, None)
                    elif isinstance(event, ConnectionTerminated):
                        client_open = False
                _flush_h2(client_ssl, client_conn)

        if upstream_ssl in readable:
            chunk = _recv_ssl(upstream_ssl)
            if not chunk:
                upstream_open = False
                client_conn.close_connection()
                _flush_h2(client_ssl, client_conn)
            else:
                for event in upstream_conn.receive_data(chunk):
                    if isinstance(event, ResponseReceived):
                        client_stream_id = upstream_to_client.get(event.stream_id)
                        if client_stream_id is None:
                            continue
                        state = streams.get(client_stream_id)
                        if state is None:
                            continue
                        status, headers = _parse_h2_response(event.headers)
                        state.response_status = status
                        state.response_headers = headers
                        client_conn.send_headers(
                            client_stream_id,
                            _build_client_response_headers(status, headers),
                            end_stream=event.stream_ended is not None,
                        )
                        _flush_h2(client_ssl, client_conn)
                        if event.stream_ended is not None:
                            _complete_response(state=state, host=host, debug=debug)
                    elif isinstance(event, DataReceived):
                        client_stream_id = upstream_to_client.get(event.stream_id)
                        if client_stream_id is None:
                            continue
                        state = streams.get(client_stream_id)
                        if state is None:
                            continue
                        state.response_body.extend(event.data)
                        if state.connect_streaming:
                            state.connect_server_buffer.extend(event.data)
                            _log_connect_frames(
                                host=host,
                                state=state,
                                debug=debug,
                                direction="server",
                                buffer=state.connect_server_buffer,
                            )
                        end_stream = event.stream_ended is not None
                        client_conn.send_data(
                            client_stream_id,
                            event.data,
                            end_stream=end_stream,
                        )
                        _flush_h2(client_ssl, client_conn)
                        if end_stream:
                            _complete_response(state=state, host=host, debug=debug)
                    elif isinstance(event, StreamEnded):
                        client_stream_id = upstream_to_client.get(event.stream_id)
                        if client_stream_id is None:
                            continue
                        state = streams.get(client_stream_id)
                        if state is not None:
                            _complete_response(state=state, host=host, debug=debug)
                    elif isinstance(event, StreamReset):
                        upstream_to_client.pop(event.stream_id, None)
                    elif isinstance(event, ConnectionTerminated):
                        upstream_open = False
                _flush_h2(upstream_ssl, upstream_conn)


def _forward_h2_stream_http11(
    *,
    client_ssl: ssl.SSLSocket,
    client_conn: H2Connection,
    state: _H2StreamState,
    host: str,
    port: int,
    debug: Any,
) -> None:
    if state.response_complete:
        return
    upstream_path = state.path
    if state.query:
        upstream_path = f"{upstream_path}?{state.query}"
    upstream_request = build_http_request(
        method=state.method,
        path=upstream_path,
        headers=state.request_headers,
        body=bytes(state.request_body),
        host=host,
    )
    try:
        upstream = upstream_connect(host, port)
    except OSError as exc:
        logger.warning("upstream connect failed host=%s: %s", host, exc)
        client_conn.send_headers(
            state.client_stream_id,
            [(b":status", b"502"), (b"content-type", b"text/plain")],
            end_stream=False,
        )
        client_conn.send_data(state.client_stream_id, str(exc).encode(), end_stream=True)
        _flush_h2(client_ssl, client_conn)
        return

    upstream.sendall(upstream_request)
    _resp_line, resp_headers, resp_body = read_http_message(upstream)
    upstream.close()

    status_parts = _resp_line.split(" ", 2)
    status = int(status_parts[1]) if len(status_parts) >= 2 else 502
    state.response_status = status
    state.response_headers = filter_header_dict(resp_headers)
    state.response_body.extend(resp_body)
    client_conn.send_headers(
        state.client_stream_id,
        _build_client_response_headers(status, state.response_headers),
        end_stream=False,
    )
    if resp_body:
        client_conn.send_data(state.client_stream_id, resp_body, end_stream=True)
    else:
        client_conn.end_stream(state.client_stream_id)
    _flush_h2(client_ssl, client_conn)
    _complete_response(state=state, host=host, debug=debug)


def _relay_h2_client_http11_upstream(
    client_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: Any,
) -> None:
    client_conn = H2Connection(config=H2Configuration(client_side=False))
    client_conn.initiate_connection()
    _flush_h2(client_ssl, client_conn)

    streams: dict[int, _H2StreamState] = {}
    client_open = True

    while client_open:
        readable, _, _ = select.select([client_ssl], [], [], RELAY_TIMEOUT)
        if not readable:
            break
        chunk = _recv_ssl(client_ssl)
        if not chunk:
            break
        for event in client_conn.receive_data(chunk):
            if isinstance(event, RequestReceived):
                method, path, query, headers = _parse_h2_request(event.headers)
                streams[event.stream_id] = _H2StreamState(
                    client_stream_id=event.stream_id,
                    method=method,
                    path=path,
                    query=query,
                    request_headers=headers,
                )
                state = streams[event.stream_id]
                _init_stream_state(state, host=host)
                if event.stream_ended is not None:
                    _complete_request(state=state, host=host, port=port, debug=debug)
                    _forward_h2_stream_http11(
                        client_ssl=client_ssl,
                        client_conn=client_conn,
                        state=state,
                        host=host,
                        port=port,
                        debug=debug,
                    )
            elif isinstance(event, DataReceived):
                state = streams.get(event.stream_id)
                if state is None:
                    continue
                state.request_body.extend(event.data)
                if event.stream_ended is not None:
                    _complete_request(state=state, host=host, port=port, debug=debug)
                    _forward_h2_stream_http11(
                        client_ssl=client_ssl,
                        client_conn=client_conn,
                        state=state,
                        host=host,
                        port=port,
                        debug=debug,
                    )
            elif isinstance(event, StreamEnded):
                state = streams.get(event.stream_id)
                if state is not None and not state.response_complete:
                    if not state.request_complete:
                        _complete_request(state=state, host=host, port=port, debug=debug)
                    _forward_h2_stream_http11(
                        client_ssl=client_ssl,
                        client_conn=client_conn,
                        state=state,
                        host=host,
                        port=port,
                        debug=debug,
                    )
            elif isinstance(event, ConnectionTerminated):
                client_open = False
        _flush_h2(client_ssl, client_conn)


def mitm_h2_relay(
    client_ssl: ssl.SSLSocket,
    host: str,
    port: int,
    debug: Any,
) -> None:
    upstream_ssl, upstream_proto = upstream_connect_with_alpn(host, port)
    try:
        if upstream_proto == "h2":
            _relay_h2_over_h2(client_ssl, upstream_ssl, host, port, debug)
        else:
            upstream_ssl.close()
            logger.info(
                "upstream %s:%s uses HTTP/1.1; relaying HTTP/2 client streams individually",
                host,
                port,
            )
            _relay_h2_client_http11_upstream(client_ssl, host, port, debug)
    finally:
        try:
            client_ssl.close()
        except OSError:
            pass
        try:
            upstream_ssl.close()
        except OSError:
            pass
