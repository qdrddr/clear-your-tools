"""TLS MITM authority and HTTP/1.1 helpers for the forward proxy."""

from __future__ import annotations

import datetime
import re
import socket
import ssl
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_HEADER_END = b"\r\n\r\n"
_CHUNKED_RE = re.compile(rb"^([0-9a-fA-F]+)\r\n", re.MULTILINE)
CONNECT_TIMEOUT = 30.0
_BENIGN_CLIENT_ERRNOS = frozenset({32, 53, 54, 57, 104})  # EPIPE, ECONNABORTED, ECONNRESET, ENOTCONN


def is_benign_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _BENIGN_CLIENT_ERRNOS:
        return True
    if isinstance(exc, ssl.SSLEOFError):
        return True
    msg = str(exc).lower()
    return "reset by peer" in msg or "broken pipe" in msg or "unexpected eof" in msg


class MitmAuthority:
    """Local CA that mints per-host TLS certificates for MITM."""

    def __init__(self, ca_cert_path: Path, ca_key_path: Path) -> None:
        if not ca_cert_path.is_file():
            raise FileNotFoundError(f"MITM CA certificate not found: {ca_cert_path}")
        if not ca_key_path.is_file():
            raise FileNotFoundError(f"MITM CA private key not found: {ca_key_path}")
        ca_cert_bytes = ca_cert_path.read_bytes()
        ca_key_bytes = ca_key_path.read_bytes()
        self._ca_cert = x509.load_pem_x509_certificate(ca_cert_bytes)
        self._ca_key = serialization.load_pem_private_key(ca_key_bytes, password=None)
        self._cache: dict[str, ssl.SSLContext] = {}

    def server_ssl_context(self, hostname: str) -> ssl.SSLContext:
        host = hostname.lower().rstrip(".")
        if host in self._cache:
            return self._cache[host]

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.UTC)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(host)]),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        ca_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pem") as cert_file:
            cert_file.write(cert_pem)
            cert_file.write(ca_pem)
            cert_path = cert_file.name
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pem") as key_file:
            key_file.write(key_pem)
            key_path = key_file.name
        ctx.load_cert_chain(cert_path, key_path)
        self._cache[host] = ctx
        return ctx

    @staticmethod
    def upstream_ssl_context() -> ssl.SSLContext:
        # Always verify upstream with public CAs (certifi), not SSL_CERT_FILE /
        # NODE_EXTRA_CA_CERTS — those may point at the MITM CA when Cursor is launched.
        import certifi

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(certifi.where())
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx


def parse_request_line(start_line: str) -> tuple[str, str, str]:
    parts = start_line.split(" ")
    if len(parts) < 2:
        raise ValueError(f"invalid request line: {start_line!r}")
    method = parts[0]
    target = parts[1]
    version = parts[2] if len(parts) > 2 else "HTTP/1.1"
    return method, target, version


def parse_host_port(target: str, default_port: int = 443) -> tuple[str, int]:
    if target.startswith(("http://", "https://")):
        parsed = urlparse(target)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port
    if ":" in target:
        host, port_str = target.rsplit(":", 1)
        return host, int(port_str)
    return target, default_port


def request_path_from_target(target: str) -> str:
    if target.startswith(("http://", "https://")):
        parsed = urlparse(target)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    if not target.startswith("/"):
        return f"/{target}"
    return target


def _recv_until(sock: socket.socket, marker: bytes, limit: int = 1024 * 1024) -> bytes:
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            raise ValueError("header block too large")
    return data


def _parse_headers(header_block: bytes) -> tuple[str, dict[str, str]]:
    lines = header_block.split(b"\r\n")
    start_line = lines[0].decode("latin-1")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        name, value = line.split(b":", 1)
        key = name.decode("latin-1").strip().lower()
        val = value.decode("latin-1").strip()
        if key in headers:
            headers[key] = f"{headers[key]}, {val}"
        else:
            headers[key] = val
    return start_line, headers


def _read_chunked_body(sock: socket.socket, initial: bytes = b"") -> bytes:
    body = initial
    while True:
        while b"\r\n" not in body:
            chunk = sock.recv(4096)
            if not chunk:
                return body
            body += chunk
        line, body = body.split(b"\r\n", 1)
        size = int(line.strip(), 16)
        if size == 0:
            if body.startswith(b"\r\n"):
                body = body[2:]
            elif body.startswith(b"\n"):
                body = body[1:]
            return body
        while len(body) < size + 2:
            chunk = sock.recv(4096)
            if not chunk:
                return body
            body += chunk
        body = body[size + 2 :]


def read_http_headers(sock: socket.socket, *, initial: bytes = b"") -> tuple[str, dict[str, str], bytes]:
    buf = initial
    while _HEADER_END not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed while reading headers")
        buf += chunk
    header_block, rest = buf.split(_HEADER_END, 1)
    start_line, headers = _parse_headers(header_block)
    return start_line, headers, rest


def read_http_body(sock: socket.socket, headers: dict[str, str], *, initial: bytes = b"") -> bytes:
    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        return _read_chunked_body(sock, initial)
    if "content-length" in headers:
        needed = int(headers["content-length"])
        body = initial
        while len(body) < needed:
            chunk = sock.recv(min(65536, needed - len(body)))
            if not chunk:
                break
            body += chunk
        return body[:needed]
    return initial


def read_http_message(sock: socket.socket, *, initial: bytes = b"") -> tuple[str, dict[str, str], bytes]:
    start_line, headers, rest = read_http_headers(sock, initial=initial)
    body = read_http_body(sock, headers, initial=rest)
    return start_line, headers, body


def build_http_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    host: str,
) -> bytes:
    lines = [f"{method} {path} HTTP/1.1"]
    out_headers = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_SKIP}
    out_headers["host"] = host
    if body and "content-length" not in out_headers:
        out_headers["content-length"] = str(len(body))
    for key, value in out_headers.items():
        lines.append(f"{key}: {value}")
    return "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n" + body


HOP_BY_HOP_SKIP = frozenset({"connection", "proxy-connection", "keep-alive", "transfer-encoding"})


def build_http_response(
    *,
    status: int,
    reason: str,
    headers: dict[str, str],
    body: bytes,
) -> bytes:
    lines = [f"HTTP/1.1 {status} {reason}"]
    out_headers: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in {"transfer-encoding", "content-length", "connection"}:
            continue
        out_headers[key] = value
    out_headers["content-length"] = str(len(body))
    out_headers["connection"] = "close"
    for key, value in out_headers.items():
        lines.append(f"{key}: {value}")
    return "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n" + body


def status_reason(status: int) -> str:
    return {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status, "Unknown")


def read_proxy_request(sock: socket.socket) -> tuple[str, str, dict[str, str], bytes]:
    data = _recv_until(sock, _HEADER_END)
    header_block, _ = data.split(_HEADER_END, 1)
    start_line, headers = _parse_headers(header_block + _HEADER_END)
    method, target, _version = parse_request_line(start_line)
    content_length = headers.get("content-length")
    body = b""
    if content_length:
        needed = int(content_length)
        body = sock.recv(needed)
        while len(body) < needed:
            chunk = sock.recv(needed - len(body))
            if not chunk:
                break
            body += chunk
    return method, target, headers, body


def filter_header_dict(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_SKIP}


def upstream_connect(host: str, port: int) -> ssl.SSLSocket:
    sock, _proto = upstream_connect_with_alpn(host, port)
    return sock


def upstream_connect_with_alpn(host: str, port: int) -> tuple[ssl.SSLSocket, str | None]:
    raw = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    ctx = MitmAuthority.upstream_ssl_context()
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    sock = ctx.wrap_socket(raw, server_hostname=host)
    return sock, sock.selected_alpn_protocol()
