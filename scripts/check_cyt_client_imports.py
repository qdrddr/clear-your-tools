#!/usr/bin/env python3
"""Fail when cyt_client imports cyt, cyt_core, or cyt.agents."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / "src" / "cyt_client"

FORBIDDEN_PREFIXES = ("cyt.", "cyt_core", "cyt.agents")


def _check_file(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cyt" or alias.name.startswith(FORBIDDEN_PREFIXES):
                    errors.append(f"{path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if node.module == "cyt" or node.module.startswith(FORBIDDEN_PREFIXES):
                errors.append(f"{path}: from {node.module} import ...")
    return errors


def main() -> int:
    errors: list[str] = []
    for path in sorted(CLIENT_ROOT.rglob("*.py")):
        errors.extend(_check_file(path))
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    print("cyt_client import check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
