"""Thin ``cyt`` entry router — dispatches fast subcommands before heavy imports."""

from __future__ import annotations

import sys


def _permissions_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "permissions":
        return None
    return argv[1:]


def _config_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "config":
        return None
    return argv[1:]


def _tiers_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "tiers":
        return None
    return argv[1:]


def main(argv: list[str] | None = None) -> None:
    """Route *argv* (default ``sys.argv[1:]``) to the appropriate CYT CLI handler."""
    cli_argv = sys.argv[1:] if argv is None else argv

    perm_argv = _permissions_argv(cli_argv)
    if perm_argv is not None:
        from cyt.permissions.cli import main as permissions_main

        permissions_main(perm_argv)
        return

    tiers_argv = _tiers_argv(cli_argv)
    if tiers_argv is not None:
        from cyt.tiers.cli import main as tiers_main

        sys.exit(tiers_main(tiers_argv))

    config_argv = _config_argv(cli_argv)
    if config_argv is not None:
        from cyt.migrations.cli import main as config_main

        config_main(config_argv)
        return

    from cyt.proxy.cli_impl import main as cli_impl_main

    cli_impl_main()
