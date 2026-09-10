"""Backward-compat shim for the unified CYT dev CLI.

Prefer :mod:`cyt.cli.app` (``uv run src/cyt/cli/app.py …``). This module delegates
to the same bootstrap + router as :mod:`cyt.cli.app`.
"""

from __future__ import annotations

from pathlib import Path

from cyt.cli.bootstrap import bootstrap_script_path
from cyt.cli.router import main


if __name__ == "__main__":
    bootstrap_script_path(Path(__file__))
    main()
