"""Bootstrap repo-local script invocations when ``cyt`` is not on ``sys.path``."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path


def repo_root_from_script(script: Path) -> Path | None:
    """Return the repo root containing ``pyproject.toml`` for *script*, if any."""
    resolved = script.resolve()
    for parent in resolved.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def bootstrap_script_path(script: Path) -> None:
    """Re-exec via ``uv run --project`` or extend ``sys.path`` before loading CYT."""
    if importlib.util.find_spec("cyt") is not None:
        return
    repo = repo_root_from_script(script)
    if repo is None:
        return
    uv = shutil.which("uv")
    if uv is not None:
        os.execv(uv, [uv, "run", "--project", str(repo), str(script.resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(repo / "src"))
