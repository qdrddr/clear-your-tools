"""Resolve CLI paths under an allowed base directory."""

from __future__ import annotations

from pathlib import Path


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


def default_cli_base() -> Path:
    """Base directory for operator-controlled CLI read/write paths."""
    return resolve_path(Path.cwd())
