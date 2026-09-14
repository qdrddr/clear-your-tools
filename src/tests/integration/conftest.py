"""Shared pytest plugins for integration tests."""

pytest_plugins = [
    "tests.support.permissions_propagation_fixtures",
    "tests.support.inject_preview_fixtures",
]
