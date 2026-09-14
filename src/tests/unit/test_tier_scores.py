"""Unit tests for Wilson scores and decay."""

from __future__ import annotations

from cyt.tiers.models import EffectiveStats
from cyt.tiers.scores import (
    decay_factor,
    demand_score,
    epoch_execution_score,
    execution_score,
    shadow_score,
    utility_score,
    wilson_lower_bound,
)


def test_wilson_lower_bound_zero_trials() -> None:
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_perfect_rate() -> None:
    value = wilson_lower_bound(10, 10)
    assert 0.7 < value <= 1.0


def test_wilson_lower_bound_small_sample_is_conservative() -> None:
    raw = 1 / 1
    bound = wilson_lower_bound(1, 1)
    assert bound < raw


def test_decay_factor() -> None:
    assert decay_factor(requests_since=0, half_life=100) == 1.0
    assert decay_factor(requests_since=100, half_life=100) == 0.5


def test_effective_stats_decay() -> None:
    stats = EffectiveStats(candidates=100, injected=40, used=10, requests_since_decay=100)
    stats.decay(half_life=100)
    assert stats.candidates == 50
    assert stats.injected == 20
    assert stats.requests_since_decay == 0


def test_demand_and_utility_scores() -> None:
    stats = EffectiveStats(candidates=100, injected=50, used=25)
    assert demand_score(stats) > 0.3
    assert utility_score(stats) > 0.3
    assert shadow_score(stats) == 0.0


def test_epoch_execution_outranks_lifetime_execution() -> None:
    stats = EffectiveStats(used=3.0, attempts=11.0, epoch_used=1.0, epoch_attempts=1.0)
    assert epoch_execution_score(stats) > execution_score(stats)
