"""Tests for permissions revision tracking and HTTP endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from cyt.hook.permissions_revision import (
    bump_permissions_revision,
    clear_permissions_revisions,
    get_permissions_revision,
)
from cyt.proxy.reverse import create_app


@pytest.fixture(autouse=True)
def _clear_revisions() -> None:
    clear_permissions_revisions()


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
async def hook_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(routes={}, config={"skills": {"enabled": False}})
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield client


def test_bump_permissions_revision_increments(ws_root: Path) -> None:
    assert get_permissions_revision("cursor", ws_root) == 0
    first = bump_permissions_revision("cursor", ws_root)
    second = bump_permissions_revision("cursor", ws_root)
    assert first == 1
    assert second == 2
    assert get_permissions_revision("cursor", ws_root) == 2


@pytest.mark.asyncio
async def test_hook_permissions_changed_endpoint(
    hook_client: httpx.AsyncClient,
    ws_root: Path,
) -> None:
    response = await hook_client.post(
        "/hook/permissions/changed",
        json={"workspace_root": str(ws_root), "agent": "cursor"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["permissions_revision"] == 1
    assert get_permissions_revision("cursor", ws_root) == 1
