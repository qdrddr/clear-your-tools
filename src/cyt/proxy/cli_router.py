"""Thin ``cyt`` entry router — dispatches fast subcommands before heavy imports."""

from __future__ import annotations

import sys


def _permissions_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "permissions":
        return None
    return argv[1:]


def main() -> None:
    perm_argv = _permissions_argv(sys.argv[1:])
    if perm_argv is not None:
        from cyt.permissions.cli import main as permissions_main

        permissions_main(perm_argv)
        return

    from cyt.proxy.cli_impl import main as cli_impl_main

    cli_impl_main()
