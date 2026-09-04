#!/usr/bin/env python3
"""Tests for read-time legacy config normalization."""

from __future__ import annotations

from cyt.migrations.base import read_schema_version
from cyt.migrations.legacy import normalize_legacy_config


def test_legacy_shim_moves_pruning_pipeline_without_stamping_version() -> None:
    cfg = {"pruning": {"pipeline": ["bm25"]}}
    out = normalize_legacy_config(cfg)
    assert out["pruning"]["tools"]["sequence"] == ["bm25"]
    assert read_schema_version(out) == "000_baseline"


def test_legacy_shim_skips_when_already_migrated() -> None:
    cfg = {
        "cyt": {"schema_version": "004_permissions_agents_layout"},
        "pruning": {"tools": {"sequence": ["llm"]}},
    }
    out = normalize_legacy_config(cfg)
    assert out is cfg or out == cfg
