"""Tests for LiteLLM quiet configuration."""

from __future__ import annotations

import pytest

from cyt.pruners import litellm_quiet


def test_configure_litellm_quiet_sets_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    litellm_quiet._configured = False
    monkeypatch.delenv("LITELLM_LOG", raising=False)

    class _FakeLiteLLM:
        set_verbose = True
        suppress_debug_info = False

    fake = _FakeLiteLLM()
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake)

    litellm_quiet.configure_litellm_quiet()
    assert fake.set_verbose is False
    assert fake.suppress_debug_info is True
    assert "LITELLM_LOG" not in __import__("os").environ

    # Idempotent: second call must not reset state.
    fake.set_verbose = True
    litellm_quiet.configure_litellm_quiet()
    assert fake.set_verbose is True
