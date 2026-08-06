"""Cloudflare hook readiness probes."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.cloudflare.readiness import cloudflare_hook_catalog_usable, probe_cloudflare_portal


def test_probe_cloudflare_portal_none_when_not_configured() -> None:
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {"enabled": True, "hook": {"tools_from": ["mcpc"]}},
        },
    }
    assert probe_cloudflare_portal(config) is None
    assert cloudflare_hook_catalog_usable(config) is False


def test_probe_cloudflare_portal_unavailable_without_url() -> None:
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {"tools_from": ["cloudflare"], "cloudflare_url": ""},
            },
        },
    }
    assert probe_cloudflare_portal(config) == "unavailable"
    assert cloudflare_hook_catalog_usable(config) is False


def test_cloudflare_hook_catalog_usable_with_disk_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cyt.cloudflare import catalog as cloudflare_catalog
    from cyt.cloudflare.catalog_disk import write_disk_catalog

    monkeypatch.setattr(
        "cyt.cloudflare.catalog_disk._CLOUDFLARE_CATALOG_CACHE_DIR",
        tmp_path,
    )
    write_disk_catalog(
        "https___mcp.example.com",
        portal_url="https://mcp.example.com",
        tools=[{"name": "context7_query-docs", "input_schema": {}}],
        content_hash="abc123",
    )
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cloudflare"],
                    "cloudflare_url": "https://mcp.example.com/mcp",
                },
            },
        },
    }

    monkeypatch.setattr(
        cloudflare_catalog,
        "resolve_credential",
        lambda *_args, **_kwargs: (None, "missing"),
    )

    assert cloudflare_hook_catalog_usable(config) is True


def test_cloudflare_hook_catalog_usable_with_credentials_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cloudflare"],
                    "cloudflare_url": "https://mcp.example.com",
                },
            },
        },
    }

    monkeypatch.setattr(
        "cyt.cloudflare.readiness.resolve_credential",
        lambda name, *, allow_prompt: ("token", "env"),
    )
    monkeypatch.setattr(
        "cyt.cloudflare.catalog.cloudflare_catalog_available_locally",
        lambda _config: False,
    )

    assert cloudflare_hook_catalog_usable(config) is True
