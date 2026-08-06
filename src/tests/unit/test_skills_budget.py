"""Tests for skills injection budget math."""

from __future__ import annotations

import pytest

from cyt.skills.budget import (
    SkillsGlobalState,
    compute_per_request_budget,
    resolve_inject_budget,
    skills_budget_precheck,
    skills_inject_allowed,
)


def _config(**overrides: str | int | float | bool) -> dict:
    skills: dict[str, str | int | float | bool | dict[str, float]] = {
        "enabled": True,
        "max_tokens_per_request": 20_000,
        "bm25_node_fallback_threshold": 50,
        "hook": {
            "request_budget_fraction": 10.0,
            "inject_cap_multiplier_of_request_tokens": 5.0,
        },
        "proxy": {
            "request_budget_fraction": 0.02,
            "inject_cap_fraction_of_savings": 0.5,
            "savings_budget_fraction": 0.1,
            "savings_rate_threshold": 0.20,
        },
    }
    skills.update(overrides)
    inject_mode = str(skills.pop("inject_via", "proxy"))
    if inject_mode == "hook":
        inject_map = dict.fromkeys(("cursor", "claude", "codex"), "hook")
    else:
        inject_map = {"cursor": "hook", "claude": "proxy", "codex": "proxy"}
    return {"skills": skills, "pruning": {"inject_via": inject_map}}


def test_inject_via_gate() -> None:
    cfg = _config(inject_via="hook")
    assert skills_inject_allowed(cfg, "hook")
    assert not skills_inject_allowed(cfg, "proxy")


def test_proxy_per_request_budget_example() -> None:
    cfg = _config()
    per_request, debug = compute_per_request_budget(
        cfg,
        "proxy",
        total_request_tokens=100_000,
        savings_tokens=10_000,
        savings_rate=0.25,
    )
    assert debug["limit_savings"] == 1_000
    assert debug["limit_request"] == 2_000
    assert debug["limit_marginal"] == 1_999
    assert per_request == 2_000


def test_global_cap_clamps_with_max_zero_remaining() -> None:
    cfg = _config()
    state = SkillsGlobalState(
        skills_injected_total=100,
        cumulative_request_tokens=1_000,
        prior_net_savings_tokens=500,
        limit_global=50,
        limit_global_remaining=0,
        bootstrap=False,
    )
    budget = resolve_inject_budget(
        cfg,
        "proxy",
        total_request_tokens=100_000,
        savings_tokens=10_000,
        savings_rate=0.25,
        budget_state=state,
    )
    assert budget.effective_max == 0


def test_bootstrap_skips_global_cap() -> None:
    cfg = _config()
    state = SkillsGlobalState(
        skills_injected_total=0,
        cumulative_request_tokens=0,
        prior_net_savings_tokens=0,
        limit_global=0,
        limit_global_remaining=0,
        bootstrap=True,
    )
    budget = resolve_inject_budget(
        cfg,
        "hook",
        total_request_tokens=1_000,
        budget_state=state,
    )
    assert budget.effective_max > 0


def test_precheck_disabled_when_max_tokens_zero() -> None:
    cfg = _config(max_tokens_per_request=0)
    assert not skills_budget_precheck(cfg)


def test_no_db_when_per_request_budget_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config()
    monkeypatch.setattr(
        "cyt.skills.budget.compute_per_request_budget",
        lambda *args, **kwargs: (0, {}),
    )

    class _FailDB:
        @staticmethod
        def open(_path: str) -> None:
            raise AssertionError("DB should not open when per_request_budget is zero")

    monkeypatch.setattr("cyt.proxy.stats.StatsDB", _FailDB)
    budget = resolve_inject_budget(
        cfg,
        "proxy",
        total_request_tokens=100_000,
        savings_tokens=10_000,
    )
    assert budget.effective_max == 0
