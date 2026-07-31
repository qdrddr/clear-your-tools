"""Resolve CLI paths under an allowed base directory."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO


def resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def require_under(path: str | Path, base: Path, *, label: str) -> Path:
    resolved = resolve_path(path)
    base_resolved = resolve_path(base)
    if not is_under(resolved, base_resolved):
        raise SystemExit(f"{label} must stay under {base_resolved}, got {resolved}")
    return resolved


def require_existing_under(path: str | Path, base: Path, *, label: str) -> Path:
    resolved = require_under(path, base, label=label)
    if not resolved.is_file():
        raise SystemExit(f"{label} not found: {resolved}")
    return resolved


def require_output_under(path: str | Path, base: Path, *, label: str) -> Path:
    resolved = require_under(path, base, label=label)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def require_repo_root(path: str | Path) -> Path:
    resolved = resolve_path(path)
    if not (resolved / "pyproject.toml").is_file():
        raise SystemExit(f"repo root not found: {resolved}")
    return resolved


def join_under(base: Path, *parts: str, label: str = "path") -> Path:
    base_resolved = resolve_path(base)
    joined = base_resolved.joinpath(*parts)
    resolved = resolve_path(joined)
    if not is_under(resolved, base_resolved):
        raise SystemExit(f"{label} must stay under {base_resolved}, got {resolved}")
    return resolved


def open_existing_under(
    path: str | Path,
    base: Path,
    *,
    label: str,
    encoding: str | None = None,
) -> TextIO:
    resolved = require_existing_under(path, base, label=label)
    return open(resolved, encoding=encoding)


def open_output_under(
    path: str | Path,
    base: Path,
    *,
    label: str,
    encoding: str = "utf-8",
) -> TextIO:
    resolved = require_output_under(path, base, label=label)
    return open(resolved, "w", encoding=encoding)


def write_text_under(
    path: str | Path,
    base: Path,
    text: str,
    *,
    label: str,
    encoding: str = "utf-8",
) -> Path:
    resolved = require_output_under(path, base, label=label)
    resolved.write_text(text, encoding=encoding)
    return resolved


def default_cli_base() -> Path:
    """Base directory for operator-controlled CLI read/write paths."""
    return resolve_path(Path.cwd())
