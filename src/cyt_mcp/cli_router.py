"""Thin ``cyt-mcp`` entry router — dispatches permissions before MCP runtime imports."""

from __future__ import annotations

import sys


def _permissions_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] != "permissions":
        return None
    return argv[1:]


def main() -> int:
    perm_argv = _permissions_argv(sys.argv[1:])
    if perm_argv is not None:
        from cyt.permissions.cli import main as permissions_main

        permissions_main(perm_argv)
        return 0

    from cyt_mcp.cli import main as cyt_mcp_main

    return cyt_mcp_main(sys.argv[1:])
