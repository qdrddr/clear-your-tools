"""Tests for reverse-proxy response forwarding (Content-Encoding pass-through)."""

from __future__ import annotations

import gzip

import httpx
import pytest
from starlette.responses import StreamingResponse

from cyt.proxy.transport import forward_upstream


def _require_streaming_response(response: object) -> StreamingResponse:
    assert isinstance(response, StreamingResponse)
    return response


async def _read_streaming_body(response: StreamingResponse) -> bytes:
    body = b""
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            body += chunk
        elif isinstance(chunk, memoryview):
            body += chunk.tobytes()
        else:
            body += chunk.encode()
    return body


@pytest.mark.asyncio
async def test_forward_upstream_passes_gzip_body_with_encoding_header() -> None:
    """Proxy must not decompress upstream bodies while leaving Content-Encoding set."""
    plain = b'{"ok":true}'
    compressed = gzip.compress(plain)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "application/json",
            },
            stream=httpx.ByteStream(compressed),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await forward_upstream(
            client,
            method="GET",
            url="http://upstream.test/",
            headers={},
            body=None,
            stream_request=False,
        )
        body = await _read_streaming_body(
            _require_streaming_response(response),
        )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert body[:2] == b"\x1f\x8b"
    assert gzip.decompress(body) == plain


@pytest.mark.asyncio
async def test_forward_upstream_identity_body_unchanged() -> None:
    plain = b'{"id":1}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(plain),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await forward_upstream(
            client,
            method="GET",
            url="http://upstream.test/",
            headers={},
            body=None,
            stream_request=False,
        )
        body = await _read_streaming_body(
            _require_streaming_response(response),
        )

    assert body == plain
    assert response.headers.get("content-encoding") is None
