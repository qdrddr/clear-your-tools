"""Enforce package boundaries between clear-your-tools and cyt-indexer-sdk."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules allowed to import cyt_indexer directly (adapter layer).
CYT_INDEXER_ADAPTER_PREFIXES: tuple[str, ...] = (
    "src/cyt/indexer/",
    "src/cyt_core/indexer/",
    "src/cyt_core/types/",
)

CYT_INDEXER_ADAPTER_FILES: frozenset[str] = frozenset(
    {
        "src/cyt_core/bootstrap.py",
    },
)

# Tests may import cyt_indexer only in documented SDK parity / boundary tests.
CYT_INDEXER_TEST_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "src/tests/test_removed_chunks.py",
        "src/tests/test_import_boundaries.py",
    },
)

SDK_SOURCE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sdk/python"),
    re.compile(r"sys\.path\.(insert|append)\s*\("),
)


def _python_sources_under(*roots: str) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        paths.extend(sorted(base.rglob("*.py")))
    return paths


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_cyt_indexer_adapter(rel_path: str) -> bool:
    if rel_path in CYT_INDEXER_ADAPTER_FILES:
        return True
    return any(rel_path.startswith(prefix) for prefix in CYT_INDEXER_ADAPTER_PREFIXES)


def _is_cyt_indexer_test_exception(rel_path: str) -> bool:
    return rel_path in CYT_INDEXER_TEST_EXCEPTIONS


def _cyt_indexer_import_violations(tree: ast.AST) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cyt_indexer" or alias.name.startswith("cyt_indexer."):
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "cyt_indexer" or node.module.startswith("cyt_indexer."):
                violations.append((node.lineno, f"from {node.module} import ..."))
    return violations


def test_no_direct_cyt_indexer_imports_outside_adapters() -> None:
    """Application code must use cyt.indexer / cyt_core adapters, not cyt_indexer."""
    offenders: list[str] = []
    for path in _python_sources_under("src/cyt", "src/cyt_core", "src/tests"):
        rel = _rel(path)
        if _is_cyt_indexer_adapter(rel) or _is_cyt_indexer_test_exception(rel):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel)
        for line_no, detail in _cyt_indexer_import_violations(tree):
            offenders.append(f"{rel}:{line_no}: {detail}")
    assert not offenders, "Direct cyt_indexer imports outside adapter layer:\n" + "\n".join(
        offenders,
    )


def test_no_monorepo_sdk_source_coupling() -> None:
    """Application code must not reference sdk/python or manipulate sys.path for the SDK."""
    offenders: list[str] = []
    for path in _python_sources_under("src/cyt", "src/cyt_core"):
        rel = _rel(path)
        if rel == "src/cyt/proxy/cli.py":
            # Re-exec helper may extend sys.path for repo checkout runs.
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern in SDK_SOURCE_MARKERS:
                if pattern.search(line):
                    offenders.append(f"{rel}:{line_no}: {line.strip()}")
                    break
    assert not offenders, "Monorepo SDK source coupling detected:\n" + "\n".join(offenders)


@pytest.mark.skipif(
    os.environ.get("CYT_ENFORCE_INSTALLED_SDK") != "1",
    reason="set CYT_ENFORCE_INSTALLED_SDK=1 to assert cyt_indexer loads from site-packages",
)
def test_cyt_indexer_loaded_from_site_packages_when_enforced() -> None:
    """Publish simulation: cyt-indexer-sdk must not resolve to sdk/python checkout."""
    import cyt_indexer

    pkg_file = Path(cyt_indexer.__file__).resolve()
    repo_sdk = (REPO_ROOT / "sdk" / "python").resolve()
    assert repo_sdk not in pkg_file.parents, (
        f"cyt_indexer must be installed from PyPI/wheel, not monorepo source\n"
        f"  package file: {pkg_file}\n"
        f"  repo sdk root: {repo_sdk}"
    )
