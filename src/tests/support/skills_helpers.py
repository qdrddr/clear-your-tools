"""Shared helpers for skills-related unit tests."""

from __future__ import annotations

from typing import Any


def isolated_skills_agents_block() -> dict[str, Any]:
    """Suppress bundled per-agent skill directories during unit tests."""
    empty: dict[str, Any] = {"skills": {"directories": []}}
    return {
        "cursor": dict(empty),
        "claude": dict(empty),
        "codex": dict(empty),
    }
