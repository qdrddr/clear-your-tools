"""Tests for tools injection allow checks."""

from __future__ import annotations

from cyt.testing.inject_via_maps import INJECT_VIA_ALL_HOOK, INJECT_VIA_DEFAULT
from cyt.tools.budget import tools_inject_allowed


def _config(*, inject_via: dict[str, str] | None = None, tools_enabled: bool = True) -> dict:
    return {
        "pruning": {
            "inject_via": inject_via or INJECT_VIA_DEFAULT,
            "tools": {"enabled": tools_enabled},
        },
    }


def test_tools_inject_allowed_respects_enabled_and_inject_via() -> None:
    cfg = _config(inject_via=INJECT_VIA_DEFAULT, tools_enabled=True)
    assert tools_inject_allowed(cfg, "proxy", agent="claude")
    assert not tools_inject_allowed(cfg, "hook", agent="claude")
    assert not tools_inject_allowed(cfg, "proxy", agent="cursor")

    disabled = _config(inject_via=INJECT_VIA_DEFAULT, tools_enabled=False)
    assert not tools_inject_allowed(disabled, "proxy", agent="claude")
    assert not tools_inject_allowed(disabled, "hook", agent="claude")

    all_hook = _config(inject_via=INJECT_VIA_ALL_HOOK, tools_enabled=True)
    assert tools_inject_allowed(all_hook, "hook", agent="cursor")
