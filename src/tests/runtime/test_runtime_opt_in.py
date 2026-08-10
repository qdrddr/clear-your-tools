"""Runtime test category smoke (opt-in only)."""

from __future__ import annotations

import pytest


@pytest.mark.runtime
def test_runtime_category_is_opt_in_only() -> None:
    assert True
