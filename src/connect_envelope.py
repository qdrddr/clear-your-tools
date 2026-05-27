"""Connect-RPC envelope parsing for MITM debug logging."""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass

CONNECT_FLAG_COMPRESSED = 0x01


@dataclass(frozen=True)
class ConnectFrame:
    compressed: bool
    payload: bytes


def is_connect_streaming(headers: dict[str, str], path: str) -> bool:
    content_type = headers.get("content-type", "").lower()
    if "connect+proto" in content_type or "connect+json" in content_type:
        return True
    if headers.get("connect-protocol-version") or headers.get("connect-content-encoding"):
        return True
    if "application/proto" in content_type and "aiserver" in path.lower():
        return True
    if any(token in path for token in ("Stream", "Bidi", "Chat", "Run", "SSE", "Unified", "Cpp")):
        return True
    if headers.get("x-cursor-streaming", "").lower() == "true":
        return True
    return False


def should_parse_connect_frames(host: str, headers: dict[str, str], path: str) -> bool:
    if is_connect_streaming(headers, path):
        return True
    if host == "api2.cursor.sh" and headers.get("content-type", "").lower().startswith("application/"):
        return True
    return False


def parse_frames(buffer: bytearray) -> list[ConnectFrame]:
    frames: list[ConnectFrame] = []
    while len(buffer) >= 5:
        flags = buffer[0]
        length = struct.unpack(">I", buffer[1:5])[0]
        if len(buffer) < 5 + length:
            break
        payload = bytes(buffer[5 : 5 + length])
        del buffer[: 5 + length]
        frames.append(ConnectFrame(compressed=bool(flags & CONNECT_FLAG_COMPRESSED), payload=payload))
    return frames


def decompress_frame_payload(frame: ConnectFrame, *, content_encoding: str | None = None) -> bytes:
    payload = frame.payload
    if frame.compressed:
        payload = gzip.decompress(payload)
    elif content_encoding and "gzip" in content_encoding.lower() and payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except OSError:
            pass
    return payload


def decode_connect_payload(raw: bytes, *, content_encoding: str | None = None) -> bytes:
    if not raw:
        return raw
    buf = bytearray(raw)
    frames = parse_frames(buf)
    if frames and not buf:
        return b"".join(
            decompress_frame_payload(frame, content_encoding=content_encoding) for frame in frames
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
