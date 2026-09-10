"""Unified dev/test entry point for all core ``cyt`` commands.

Examples::

    uv run src/cyt/cli/app.py tiers status
    uv run src/cyt/cli/app.py hook daemon restart
    uv run src/cyt/cli/app.py proxy --port 8834
    uv run src/cyt/cli/app.py config current
"""

from __future__ import annotations

from pathlib import Path

from cyt.cli.bootstrap import bootstrap_script_path
from cyt.cli.router import main


if __name__ == "__main__":
    bootstrap_script_path(Path(__file__))
    main()
