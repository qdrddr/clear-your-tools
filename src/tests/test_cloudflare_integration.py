"""Live Cloudflare MCP portal integration tests (opt-in)."""

from __future__ import annotations

import os

import pytest

from cyt.launch.secrets import resolve_credential
from tests.test_credential_helpers import CI_CREDENTIAL_STUBS

pytestmark = pytest.mark.integration


def _require_live_cloudflare() -> tuple[str, str, str]:
    mcp_url = os.environ.get("CF_MCP_SERVER_URL", "").strip()
    if not mcp_url:
        pytest.skip("CF_MCP_SERVER_URL not set")
    client_id, _ = resolve_credential("CF_ACCESS_CLIENT_ID", allow_prompt=False)
    client_secret, _ = resolve_credential("CF_ACCESS_CLIENT_SECRET", allow_prompt=False)
    if (
        not client_id
        or not client_secret
        or client_id in CI_CREDENTIAL_STUBS.values()
        or client_secret in CI_CREDENTIAL_STUBS.values()
    ):
        pytest.skip("CF Access credentials not configured for live cloudflare tests")
    base_url = mcp_url.removesuffix("/mcp").rstrip("/")
    return base_url, client_id, client_secret


def test_live_mcp_initialize_and_tools_list() -> None:
    import asyncio

    from cyt.cloudflare.mcp import EXCLUDED_TOOL_NAMES, fetch_cloudflare_tools_list_async

    base_url, client_id, client_secret = _require_live_cloudflare()
    tools = asyncio.run(
        fetch_cloudflare_tools_list_async(
            portal_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
        ),
    )
    assert tools
    names = {tool["name"] for tool in tools}
    assert not names & EXCLUDED_TOOL_NAMES
