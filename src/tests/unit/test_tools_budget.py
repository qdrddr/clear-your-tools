"""Tests for tools injection allow checks."""

from __future__ import annotations

from cyt.tools.budget import tools_inject_allowed


def _config(*, inject_via: str = "proxy", tools_enabled: bool = True) -> dict:
    return {
        "pruning": {
            "inject_via": inject_via,
            "tools": {"enabled": tools_enabled},
        },
    }


def test_tools_inject_allowed_respects_enabled_and_inject_via() -> None:
    cfg = _config(inject_via="proxy", tools_enabled=True)
    assert tools_inject_allowed(cfg, "proxy")
    assert not tools_inject_allowed(cfg, "hook")

    disabled = _config(inject_via="proxy", tools_enabled=False)
    assert not tools_inject_allowed(disabled, "proxy")
    assert not tools_inject_allowed(disabled, "hook")
