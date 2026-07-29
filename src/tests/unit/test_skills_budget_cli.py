"""Tests for cyt skills budget report."""

from __future__ import annotations

from cyt.skills.budget import format_skills_budget_report, skills_budget_report_json


def test_budget_report_disabled_skills() -> None:
    config = {
        "skills": {
            "enabled": False,
            "inject_via": "proxy",
            "max_tokens_per_request": 20_000,
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
        },
        "stats": {"database": {"path": ":memory:"}},
    }
    text = format_skills_budget_report(config)
    assert "skills.enabled: False" in text
    assert "injection will work: no" in text
    payload = skills_budget_report_json(config)
    assert payload["skills_enabled"] is False
