"""Shared pytest plugins for integration tests."""

from __future__ import annotations

import pytest

# Integration tests are manual-only (see src/tests/conftest.py skip logic).
pytestmark = pytest.mark.integration

pytest_plugins = [
    "tests.support.permissions_propagation_fixtures",
]
