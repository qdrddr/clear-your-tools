"""Skills pageindex (markdown tree indexing and retrieval)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt_indexer._native import SkillsBuilder as _SkillsBuilder
from cyt_indexer._native import build_skills_index as _build_skills_index
from cyt_indexer._native import get_skill_document as _get_skill_document
from cyt_indexer._native import get_skill_line_content_from_spec as _get_skill_line_content_from_spec
from cyt_indexer._native import get_skill_structure as _get_skill_structure
from cyt_indexer._native import load_skills_index_from_dir as _load_skills_index_from_dir
from cyt_indexer._native import md_to_tree as _md_to_tree
from cyt_indexer._native import skills_index_from_decomposed_dir as _skills_index_from_decomposed_dir
from cyt_indexer._native import write_skills_index as _write_skills_index


@dataclass
class PageIndexConfig:
    if_add_node_id: bool = True
    if_add_node_text: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "if_add_node_id": self.if_add_node_id,
            "if_add_node_text": self.if_add_node_text,
        }


def default_page_index_config() -> PageIndexConfig:
    return PageIndexConfig()


def build_skills_index(
    skill_dirs: list[str],
    *,
    config: PageIndexConfig | dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _config_dict(config)
    return _build_skills_index(skill_dirs, cfg)


def write_skills_index(index: dict[str, Any], output_dir: str) -> None:
    _write_skills_index(index, output_dir)


def load_skills_index_from_dir(catalog_dir: str) -> dict[str, Any]:
    return _load_skills_index_from_dir(catalog_dir)


def skills_index_from_decomposed_dir(dir_path: str) -> dict[str, Any]:
    return _skills_index_from_decomposed_dir(dir_path)


def md_to_tree(
    markdown_content: str,
    source_path: str,
    *,
    config: PageIndexConfig | dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _config_dict(config)
    return _md_to_tree(markdown_content, source_path, cfg)


def get_skill_document(documents: dict[str, Any], doc_id: str) -> dict[str, Any]:
    return _get_skill_document(documents, doc_id)


def get_skill_structure(documents: dict[str, Any], doc_id: str) -> list[Any] | dict[str, Any]:
    return _get_skill_structure(documents, doc_id)


def get_skill_line_content_from_spec(
    index: dict[str, Any],
    doc_id: str,
    line_num_spec: str,
) -> list[dict[str, Any]]:
    return _get_skill_line_content_from_spec(index, doc_id, line_num_spec)


class SkillsBuilder:
    def __init__(self, *, memory_only: bool = True, output_dir: str | None = None) -> None:
        self._inner = _SkillsBuilder(memory_only=memory_only, output_dir=output_dir)

    def build_from_dirs(
        self,
        skill_dirs: list[str],
        *,
        config: PageIndexConfig | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = _config_dict(config)
        return self._inner.build_from_dirs(skill_dirs, cfg)

    def write_catalog(self) -> dict[str, Any]:
        return self._inner.write_catalog()

    def to_skills_index_json(self) -> dict[str, Any]:
        return self._inner.to_skills_index_json()

    def to_skills_dict(self) -> dict[str, Any]:
        return self._inner.to_skills_dict()


def _config_dict(config: PageIndexConfig | dict[str, Any] | None) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, PageIndexConfig):
        return config.to_dict()
    return config
