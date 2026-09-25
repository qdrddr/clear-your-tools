"""Paid test category smoke (opt-in only)."""

from __future__ import annotations

import pytest


@pytest.mark.paid
def test_paid_category_is_opt_in_only() -> None:
    assert True
