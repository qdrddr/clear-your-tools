"""Tests for executor HTTP tool source."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any
from unittest.mock import patch

import httpx

from cyt.tools.sources.executor_http import fetch_executor_tools


def test_fetch_executor_tools_normalizes_list_and_schema() -> None:
    config = {
        "pruning": {
            "tools": {
                "hook": {
                    "executor_url": "http://localhost:4789",
                    "executor_token_var": "EXECUTOR_TOKEN",
                },
            },
        },
    }
    expected = [
        {
            "name": "tools.demo.org.default.search",
            "description": "Search files",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    ]

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("secret-token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.asyncio.run",
            side_effect=lambda coro: (
                coro.close(),
                expected,
            )[1],
        ) as run_mock,
    ):
        tools = fetch_executor_tools(config)

    assert tools == expected
    run_mock.assert_called_once()


def test_fetch_executor_tools_returns_empty_on_http_error() -> None:
    config = {
        "pruning": {
            "tools": {
                "hook": {
                    "executor_url": "http://localhost:4789",
                    "executor_token_var": "EXECUTOR_TOKEN",
                },
            },
        },
    }

    def _raise_http_error(coro: Coroutine[Any, Any, Any]) -> list[dict[str, object]]:
        coro.close()
        raise httpx.HTTPError("boom")

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.asyncio.run",
            side_effect=_raise_http_error,
        ),
    ):
        assert fetch_executor_tools(config) == []
