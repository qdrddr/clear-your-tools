#!/usr/bin/env python3
"""Tests for revision 002 — pruning.tools namespace migration."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import read_schema_version
from cyt.migrations.versions import load_revision_modules


def _upgrade_002(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(m for m in load_revision_modules() if m.revision == "002_pruning_tools_namespace")
    return module.upgrade(cfg, scope="global")


def test_moves_pipeline_to_tools_sequence() -> None:
    cfg = {"pruning": {"pipeline": ["rerank", "llm"]}}
    out = _upgrade_002(cfg)
    assert out["pruning"]["tools"]["sequence"] == ["rerank", "llm"]
    assert "pipeline" not in out["pruning"]


def test_moves_policy_and_per_tool() -> None:
    cfg = {
        "pruning": {
            "policy": {"system_tool": "prune_optional"},
            "per_tool": {"Agent": "always_include"},
        },
    }
    out = _upgrade_002(cfg)
    assert out["pruning"]["tools"]["policy"]["system_tool"] == "prune_optional"
    assert out["pruning"]["tools"]["policy"]["per_tool"]["Agent"] == "always_include"
    assert "policy" not in out["pruning"]
    assert "per_tool" not in out["pruning"]


def test_moves_stage_block_and_model_nick() -> None:
    cfg = {
        "pruning": {
            "llm": {
                "model": {"remote": {"model_nick": "mercury-2"}},
                "score_tool": 1,
            },
        },
    }
    out = _upgrade_002(cfg)
    llm = out["pruning"]["tools"]["pipelines"]["llm"]
    assert llm["model_nick"] == "mercury-2"
    assert llm["score_tool"] == 1
    assert "llm" not in out["pruning"]


def test_does_not_overwrite_existing_canonical_keys() -> None:
    cfg = {
        "pruning": {
            "pipeline": ["bm25"],
            "tools": {"sequence": ["llm"]},
        },
    }
    out = _upgrade_002(cfg)
    assert out["pruning"]["tools"]["sequence"] == ["llm"]


def test_idempotent_when_canonical_already_present() -> None:
    cfg = {
        "pruning": {
            "tools": {
                "sequence": ["bm25"],
                "policy": {"minimum_tools": 50},
            },
        },
    }
    out = _upgrade_002(cfg)
    assert out["pruning"]["tools"]["sequence"] == ["bm25"]
    assert read_schema_version(out) == "002_pruning_tools_namespace"
