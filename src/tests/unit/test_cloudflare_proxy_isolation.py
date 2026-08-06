"""Proxy mode must not touch Cloudflare portal catalog."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.cloudflare.catalog import get_cloudflare_catalog
from cyt.testing.inject_via_maps import INJECT_VIA_ALL_PROXY

_PROXY_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": dict(INJECT_VIA_ALL_PROXY),
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "cloudflare",
                "cloudflare_url": "https://mcp.example.com",
            },
        },
    },
}


def test_get_cloudflare_catalog_returns_none_in_proxy_mode() -> None:
    with patch(
        "cyt.cloudflare.catalog._blocking_network_fetch",
        side_effect=AssertionError("cloudflare must not fetch in proxy mode"),
    ):
        assert get_cloudflare_catalog(_PROXY_CONFIG, blocking=True) is None
