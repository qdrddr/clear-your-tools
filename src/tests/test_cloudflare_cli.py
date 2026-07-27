"""Tests for ``cyt cloudflare save`` CLI dispatch."""

from __future__ import annotations

import argparse

from cyt.proxy.cli_impl import _dispatch_cli_command


def test_dispatch_cloudflare_save_routes_to_handler() -> None:
    called: list[argparse.Namespace] = []

    def handler(args: argparse.Namespace) -> None:
        called.append(args)

    args = argparse.Namespace(
        command="cloudflare",
        cloudflare_command="save",
        cloudflare_handler=handler,
    )
    assert _dispatch_cli_command(args) is True
    assert called == [args]
