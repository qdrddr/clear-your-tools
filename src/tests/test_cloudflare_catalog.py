"""Cloudflare catalog disk + config helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt import config as configs
from cyt.cloudflare.catalog_disk import (
    normalize_cloudflare_url_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)


def test_normalize_cloudflare_url_slug() -> None:
    slug = normalize_cloudflare_url_slug("https://mcp.example.com/mcp")
    assert slug == "https___mcp.example.com"


def test_cache_key_normalizes_portal_url_variants() -> None:
    from cyt.cloudflare.catalog import _cache_key_for_config

    base_config = {
        "pruning": {
            "tools": {
                "hook": {"cloudflare_url": "https://mcp.example.com"},
            },
        },
    }
    mcp_config = {
        "pruning": {
            "tools": {
                "hook": {"cloudflare_url": "https://mcp.example.com/mcp"},
            },
        },
    }
    assert _cache_key_for_config(base_config) == _cache_key_for_config(mcp_config)


def test_disk_catalog_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.cloudflare.catalog_disk._CLOUDFLARE_CATALOG_CACHE_DIR",
        tmp_path,
    )
    tools = [
        {
            "name": "context7_query-docs",
            "description": "docs",
            "input_schema": {"type": "object"},
            "cloudflare_server_id": "context7",
        },
    ]
    content_hash = raw_catalog_content_hash(tools)
    write_disk_catalog(
        "example",
        portal_url="https://mcp.example.com",
        tools=tools,
        content_hash=content_hash,
    )
    envelope = read_disk_catalog("example")
    assert envelope is not None
    assert envelope["tool_count"] == 1
    assert envelope["tools"][0]["name"] == "context7_query-docs"


def test_load_catalog_from_disk_filters_excluded_portal_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cloudflare.catalog import (
        _cache_key_for_config,
        _get_state,
        clear_cloudflare_catalog_cache,
        load_cloudflare_catalog_from_disk,
    )
    from cyt.cloudflare.mcp import EXCLUDED_TOOL_NAMES

    monkeypatch.setattr(
        "cyt.cloudflare.catalog_disk._CLOUDFLARE_CATALOG_CACHE_DIR",
        tmp_path,
    )
    clear_cloudflare_catalog_cache()
    tools = [
        {"name": "context7_query-docs", "input_schema": {}},
        {"name": "portal_list_servers", "input_schema": {}},
    ]
    write_disk_catalog(
        "https___mcp.example.com",
        portal_url="https://mcp.example.com",
        tools=tools,
        content_hash=raw_catalog_content_hash(tools),
    )
    config = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cloudflare"],
                    "cloudflare_url": "https://mcp.example.com/mcp",
                },
            },
        },
    }
    assert load_cloudflare_catalog_from_disk(config) is True
    cache_key = _cache_key_for_config(config)
    state = _get_state(cache_key)
    names = {tool["name"] for tool in state.tools}
    assert names == {"context7_query-docs"}
    assert not names & EXCLUDED_TOOL_NAMES


def test_required_tools_hook_env_var_names_cloudflare() -> None:
    config = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cloudflare"],
                    "cloudflare_url": "https://mcp.example.com",
                },
            },
        },
    }
    names = configs.required_tools_hook_env_var_names(config)
    assert names == ["CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"]


def test_resolve_access_credentials_uses_configured_var_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cloudflare.catalog import _resolve_access_credentials

    resolved: list[str] = []

    def fake_resolve(name: str, *, allow_prompt: bool) -> tuple[str, str]:
        resolved.append(name)
        return f"value-for-{name}", "env"

    monkeypatch.setattr("cyt.cloudflare.catalog.resolve_credential", fake_resolve)
    config = {
        "pruning": {
            "tools": {
                "hook": {
                    "cloudflare_access_client_id_var": "MY_CF_ID",
                    "cloudflare_access_client_secret_var": "MY_CF_SECRET",  # pragma: allowlist secret
                },
            },
        },
    }
    client_id, client_secret = _resolve_access_credentials(config, allow_prompt=False)
    assert resolved == ["MY_CF_ID", "MY_CF_SECRET"]
    assert client_id == "value-for-MY_CF_ID"
    assert client_secret == "value-for-MY_CF_SECRET"  # pragma: allowlist secret
