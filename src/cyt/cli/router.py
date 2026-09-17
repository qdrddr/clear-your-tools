"""Thin ``cyt`` entry router — dispatches fast subcommands before heavy imports."""

from __future__ import annotations

import sys

from cyt.cli.exit import run_main


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


def _inject_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "inject":
        return None
    return argv[1:]


def _db_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "db":
        return None
    return argv[1:]


def main(argv: list[str] | None = None) -> None:
    """Route *argv* (default ``sys.argv[1:]``) to the appropriate CYT CLI handler."""
    run_main(_route, argv)


def _route(argv: list[str] | None = None) -> None:
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

    db_argv = _db_argv(cli_argv)
    if db_argv is not None:
        from cyt.db.cli import main as db_main

        sys.exit(db_main(db_argv))

    inject_argv = _inject_argv(cli_argv)
    if inject_argv is not None:
        from cyt.tools.inject_cli import main as inject_main

        sys.exit(inject_main(inject_argv))

    config_argv = _config_argv(cli_argv)
    if config_argv is not None:
        from cyt.migrations.cli import main as config_main

        config_main(config_argv)
        return

    from cyt.proxy.cli_impl import main as cli_impl_main

    cli_impl_main()
