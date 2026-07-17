"""Ensure cyt.mcpc has no imports from cyt.executor."""

from __future__ import annotations

from pathlib import Path


def test_mcpc_package_has_no_executor_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "cyt" / "mcpc"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "cyt.executor" in text:
            offenders.append(str(path.relative_to(root.parent)))
    assert offenders == []
