"""Pytest hooks for optional local debug-snapshot runs."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--file",
        action="store",
        default=None,
        help="Path to a proxy debug snapshot JSON (e.g. debug/full_example.json)",
    )
    parser.addoption(
        "--output",
        action="store",
        default=None,
        help="Write decomposed catalog JSON output to this file",
    )


@pytest.fixture
def example_file(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("--file")


@pytest.fixture
def output_file(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("--output")
