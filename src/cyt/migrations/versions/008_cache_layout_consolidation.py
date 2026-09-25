"""Config schema revision 008 — consolidate cache dirs under ~/.config/cyt/cache/."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import (
    ConfigScope,
    deep_copy_config,
    ensure_dict,
    get_path,
    set_path,
    set_schema_stamp,
)

revision = "008_cache_layout_consolidation"
down_revision = "007_config_layout_restructure"
applies_to = "both"

_OLD_BM25 = "~/.config/cyt/bm25"
_OLD_SKILLS_CACHE = "~/.config/cyt/skills"
_OLD_TOOLS = "~/.config/cyt/tools"
_NEW_BM25 = "~/.config/cyt/cache/bm25"
_NEW_SKILLS_CACHE = "~/.config/cyt/cache/skills"
_NEW_TOOLS = "~/.config/cyt/cache/tools"


def _rewrite_path(value: object, *, old: str, new: str) -> object:
    if isinstance(value, str) and value.strip() == old:
        return new
    return value


def _migrate_cache_paths(cfg: dict[str, Any]) -> None:
    cache = ensure_dict(cfg, "cache")
    for key, old, new in (
        ("bm25_dir", _OLD_BM25, _NEW_BM25),
        ("skills_dir", _OLD_SKILLS_CACHE, _NEW_SKILLS_CACHE),
        ("tools_dir", _OLD_TOOLS, _NEW_TOOLS),
    ):
        current = cache.get(key)
        if current is not None:
            cache[key] = _rewrite_path(current, old=old, new=new)


def _migrate_bm25_index_dirs(cfg: dict[str, Any]) -> None:
    models = cfg.get("models")
    if isinstance(models, dict):
        bm25 = models.get("bm25")
        if isinstance(bm25, dict) and bm25.get("index_dir") is not None:
            bm25["index_dir"] = _rewrite_path(
                bm25["index_dir"],
                old=_OLD_BM25,
                new=_NEW_BM25,
            )

    tools = cfg.get("tools")
    if isinstance(tools, dict):
        pipelines = tools.get("pipelines")
        if isinstance(pipelines, dict):
            bm25 = pipelines.get("bm25")
            if isinstance(bm25, dict) and bm25.get("index_dir") is not None:
                bm25["index_dir"] = _rewrite_path(
                    bm25["index_dir"],
                    old=_OLD_BM25,
                    new=_NEW_BM25,
                )

    legacy = get_path(cfg, "pruning", "tools", "pipelines", "bm25", "index_dir")
    if legacy is not None:
        set_path(
            cfg,
            _rewrite_path(legacy, old=_OLD_BM25, new=_NEW_BM25),
            "pruning",
            "tools",
            "pipelines",
            "bm25",
            "index_dir",
        )


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    _migrate_cache_paths(result)
    _migrate_bm25_index_dirs(result)
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 008_cache_layout_consolidation")
