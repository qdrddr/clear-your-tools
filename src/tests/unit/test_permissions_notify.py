"""Tests for permissions change notifier."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cyt.permissions.notify import notify_permissions_changed, resolve_permissions_changed_url


def test_notify_permissions_changed_posts_to_hook(tmp_path: Path) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    posted: list[dict[str, object]] = []

    def fake_post(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        posted.append({"url": url, "payload": payload})
        return 200, {"status": "ok", "permissions_revision": 1}

    with (
        patch(
            "cyt.permissions.notify.resolve_permissions_changed_url",
            return_value="http://127.0.0.1:8834/hook/permissions/changed",
        ),
        patch("cyt.permissions.notify.urlopen") as urlopen,
    ):
        response = type(
            "Resp",
            (),
            {
                "getcode": lambda self: 200,
                "read": lambda self: b'{"status":"ok"}',
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
            },
        )()
        urlopen.return_value = response
        ok = notify_permissions_changed(workspace_root=ws, agent="cursor")

    assert ok is True
    assert urlopen.called


def test_resolve_permissions_changed_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "CYT_HOOK_URL",
        "http://127.0.0.1:8834/hook/connect",
    )
    url = resolve_permissions_changed_url()
    assert url == "http://127.0.0.1:8834/hook/permissions/changed"
