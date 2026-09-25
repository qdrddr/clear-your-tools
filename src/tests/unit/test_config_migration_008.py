"""Migration revision 008 — cache layout consolidation."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import read_schema_version
from cyt.migrations.versions import load_revision_modules


def _upgrade_008(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(
        m for m in load_revision_modules() if m.revision == "008_cache_layout_consolidation"
    )
    return module.upgrade(cfg, scope="user")


def test_upgrade_rewrites_legacy_cache_paths() -> None:
    cfg = {
        "cache": {
            "bm25_dir": "~/.config/cyt/bm25",
            "skills_dir": "~/.config/cyt/skills",
            "tools_dir": "~/.config/cyt/tools",
        },
        "models": {"bm25": {"index_dir": "~/.config/cyt/bm25"}},
        "tools": {"pipelines": {"bm25": {"index_dir": "~/.config/cyt/bm25"}}},
    }

    out = _upgrade_008(cfg)

    assert out["cache"]["bm25_dir"] == "~/.config/cyt/cache/bm25"
    assert out["cache"]["skills_dir"] == "~/.config/cyt/cache/skills"
    assert out["cache"]["tools_dir"] == "~/.config/cyt/cache/tools"
    assert out["models"]["bm25"]["index_dir"] == "~/.config/cyt/cache/bm25"
    assert out["tools"]["pipelines"]["bm25"]["index_dir"] == "~/.config/cyt/cache/bm25"
    assert read_schema_version(out) == "008_cache_layout_consolidation"


def test_upgrade_preserves_custom_paths() -> None:
    cfg = {
        "cache": {
            "tools_dir": "/custom/tools",
        },
    }
    out = _upgrade_008(cfg)
    assert out["cache"]["tools_dir"] == "/custom/tools"
