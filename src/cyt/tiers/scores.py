"""Wilson lower bound and request-based decay helpers."""

from __future__ import annotations

import math


def wilson_lower_bound(successes: float, trials: float, *, z: float = 1.96) -> float:
    """Conservative lower bound for a Bernoulli rate."""
    if trials <= 0:
        return 0.0
    if successes < 0:
        successes = 0.0
    if successes > trials:
        successes = trials
    phat = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    center = phat + z2 / (2.0 * trials)
    margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * trials)) / trials)
    return max(0.0, (center - margin) / denom)


def decay_factor(*, requests_since: int, half_life: float) -> float:
    if requests_since <= 0 or half_life <= 0:
        return 1.0
    return 2.0 ** (-requests_since / half_life)


def demand_score(stats: object) -> float:
    from cyt.tiers.models import EffectiveStats

    if not isinstance(stats, EffectiveStats):
        return 0.0
    return wilson_lower_bound(stats.injected, stats.candidates)


def utility_score(stats: object) -> float:
    from cyt.tiers.models import EffectiveStats

    if not isinstance(stats, EffectiveStats):
        return 0.0
    return wilson_lower_bound(stats.used, stats.injected)


def shadow_score(stats: object) -> float:
    from cyt.tiers.models import EffectiveStats

    if not isinstance(stats, EffectiveStats):
        return 0.0
    return wilson_lower_bound(stats.shadow_hits, stats.shadow_evaluations)
