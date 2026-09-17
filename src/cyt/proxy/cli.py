"""Backward-compat shim for the unified CYT dev CLI.

Prefer :mod:`cyt.cli.app` (``uv run src/cyt/cli/app.py …``). This module delegates
to the same bootstrap + router as :mod:`cyt.cli.app`.
"""

from __future__ import annotations

if __name__ == "__main__":
    try:
        from cyt.cli.exit import run_main

        def _main() -> None:
            from pathlib import Path

            from cyt.cli.bootstrap import bootstrap_script_path
            from cyt.cli.router import main

            bootstrap_script_path(Path(__file__))
            main()

        run_main(_main)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
