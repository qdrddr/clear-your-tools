#!/usr/bin/env python3
"""Tests for revision 006 — policies catalog schema stamp."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import read_schema_version
from cyt.migrations.versions import load_revision_modules


def _upgrade_006(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(m for m in load_revision_modules() if m.revision == "006_policies_stubs_schema")
    return module.upgrade(cfg, scope="global")


def test_stamps_schema_version() -> None:
    out = _upgrade_006({"pruning": {"tools": {"policy": {"system_tool": "prune_optional"}}}})
    assert read_schema_version(out) == "006_policies_stubs_schema"


def test_preserves_user_policies_overlay() -> None:
    custom = [{"name": "custom", "mode": "always_include"}]
    out = _upgrade_006({"policies": custom})
    assert out["policies"] == custom


def test_moves_legacy_pruning_policy_keys() -> None:
    out = _upgrade_006(
        {
            "pruning": {
                "policy": {"system_tool": "prune_optional"},
                "per_tool": {"Agent": "always_include"},
            },
        },
    )
    assert out["pruning"]["tools"]["policy"]["system_tool"] == "prune_optional"
    assert out["pruning"]["tools"]["policy"]["per_tool"]["Agent"] == "always_include"
