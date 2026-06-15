"""Script-path entry point for the reverse HTTP proxy CLI.

``uv run /path/to/cli.py`` and ``python -m cyt.proxy.cli`` load this module as
``__main__``. It bootstraps the repo project when ``cyt`` is not importable, then
delegates to :mod:`cyt.proxy.cli_impl`.
"""

from __future__ import annotations

import importlib.util
import os
import runpy
import shutil
import sys
from pathlib import Path

_IMPL = Path(__file__).with_name("cli_impl.py")


def _bootstrap_script_path() -> None:
    """Re-exec via ``uv run --project`` or extend ``sys.path`` before loading ``cli_impl``."""
    if importlib.util.find_spec("cyt") is not None:
        return
    script = Path(__file__).resolve()
    repo = script.parents[3]
    if not (repo / "pyproject.toml").is_file():
        return
    uv = shutil.which("uv")
    if uv is not None:
        os.execv(uv, [uv, "run", "--project", str(repo), str(script), *sys.argv[1:]])
    sys.path.insert(0, str(repo / "src"))


if __name__ == "__main__":
    _bootstrap_script_path()
    runpy.run_path(str(_IMPL), run_name="__main__")
