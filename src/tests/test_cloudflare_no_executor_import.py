"""Ensure cyt.cloudflare has no imports from cyt.executor or cyt.mcpc."""

from __future__ import annotations

from pathlib import Path


def test_cloudflare_package_has_no_cross_source_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "cyt" / "cloudflare"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "cyt.executor" in text or "cyt.mcpc" in text:
            offenders.append(str(path.relative_to(root.parent)))
    assert offenders == []
