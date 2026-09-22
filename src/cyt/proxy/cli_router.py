"""Installed ``cyt`` console-script entry point (re-exports unified router)."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> None:
    import os

    os.environ.setdefault("CYT_NO_AUTO_BOOTSTRAP", "1")
    try:
        from cyt.cli.exit import run_main

        def _main() -> None:
            from cyt.cli.router import main as router_main

            router_main(argv)

        run_main(_main)
    except KeyboardInterrupt:
        raise SystemExit(130) from None


__all__ = ["main"]
