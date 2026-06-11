"""Skills pageindex (markdown tree indexing and retrieval)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt_indexer.bm25_cohesion import Bm25CohesionConfig, default_bm25_cohesion_config
from cyt_indexer.bm25_cohesion import cohesion_config_dict as _cohesion_config_dict
from cyt_indexer._native import ReconstructOptions as _ReconstructOptions
from cyt_indexer._native import SkillsBuilder as _SkillsBuilder
from cyt_indexer._native import build_skills_index as _build_skills_index
from cyt_indexer._native import get_skill_content_retrieve_result as _get_skill_content_retrieve_result
from cyt_indexer._native import get_skill_document as _get_skill_document
from cyt_indexer._native import get_skill_line_content as _get_skill_line_content
from cyt_indexer._native import get_skill_line_content_from_spec as _get_skill_line_content_from_spec
from cyt_indexer._native import get_skill_structure as _get_skill_structure
from cyt_indexer._native import load_skills_index_from_dir as _load_skills_index_from_dir
from cyt_indexer._native import md_to_tree as _md_to_tree
from cyt_indexer._native import parse_skill_chunk_ids as _parse_skill_chunk_ids
from cyt_indexer._native import parse_skill_node_ids as _parse_skill_node_ids
from cyt_indexer._native import reconstruct_skill_markdown as _reconstruct_skill_markdown
from cyt_indexer._native import repair_skill_chunks as _repair_skill_chunks
from cyt_indexer._native import skills_index_from_decomposed_dir as _skills_index_from_decomposed_dir
from cyt_indexer._native import write_reconstructed_skill as _write_reconstructed_skill
from cyt_indexer._native import write_skills_index as _write_skills_index


@dataclass
class ReconstructOptions:
    keep_all_headers: bool = False

    def to_native(self) -> _ReconstructOptions:
        return _ReconstructOptions(keep_all_headers=self.keep_all_headers)


@dataclass
class PageIndexConfig:
    if_add_node_id: bool = True
    if_add_node_text: bool = False
    enable_bm25_chunking: bool = True
    bm25_cohesion: Bm25CohesionConfig | dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "if_add_node_id": self.if_add_node_id,
            "if_add_node_text": self.if_add_node_text,
            "enable_bm25_chunking": self.enable_bm25_chunking,
        }
        cohesion = _cohesion_config_dict(self.bm25_cohesion)
        if cohesion is not None:
            out["bm25_cohesion"] = cohesion
        elif self.bm25_cohesion is None:
            out["bm25_cohesion"] = default_bm25_cohesion_config().to_dict()
        return out

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> PageIndexConfig:
        """Build from a partial mapping; unset keys keep SDK defaults when passed to Rust."""
        if not mapping:
            return default_page_index_config()
        cfg = default_page_index_config()
        if "if_add_node_id" in mapping:
            cfg.if_add_node_id = bool(mapping["if_add_node_id"])
        if "if_add_node_text" in mapping:
            cfg.if_add_node_text = bool(mapping["if_add_node_text"])
        if "enable_bm25_chunking" in mapping:
            cfg.enable_bm25_chunking = bool(mapping["enable_bm25_chunking"])
        if "bm25_cohesion" in mapping:
            bm25 = mapping["bm25_cohesion"]
            if isinstance(bm25, dict):
                base = default_bm25_cohesion_config().to_dict()
                base.update(bm25)
                cfg.bm25_cohesion = base
            elif isinstance(bm25, Bm25CohesionConfig):
                cfg.bm25_cohesion = bm25
        elif any(k in mapping for k in _BM25_FLAT_KEYS):
            base = default_bm25_cohesion_config().to_dict()
            for key in _BM25_FLAT_KEYS:
                if key in mapping:
                    base[key] = mapping[key]
            cfg.bm25_cohesion = base
        return cfg


PageIndexConfigInput = PageIndexConfig | dict[str, Any]

_BM25_FLAT_KEYS = frozenset(
    {
        "window_mode",
        "threshold",
        "merge_threshold",
        "chunk_size",
        "token_counter",
        "similarity_window",
        "next_unit_size",
        "skip_window",
        "min_units_per_chunk",
        "minimum_words",
        "minimum_sentences",
        "min_characters_per_sentence",
        "min_characters_per_word",
        "delimiters",
        "include_delim",
        "use_stopwords",
        "filter_window",
        "filter_polyorder",
        "filter_tolerance",
        "stem_language",
    },
)


def default_page_index_config() -> PageIndexConfig:
    return PageIndexConfig(bm25_cohesion=default_bm25_cohesion_config())


def page_index_config_without_chunking() -> PageIndexConfig:
    """Pageindex config with one full-text chunk per node (no BM25 splitting)."""
    return PageIndexConfig(enable_bm25_chunking=False, bm25_cohesion=default_bm25_cohesion_config())


def page_index_config_from_mapping(mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Partial pageindex settings from app/YAML; Rust merges unset keys with SDK defaults."""
    return PageIndexConfig.from_mapping(mapping).to_dict()


def build_skills_index(
    skill_dirs: list[str],
    *,
    config: PageIndexConfigInput | None = None,
) -> dict[str, Any]:
    cfg = _config_dict(config)
    return _build_skills_index(skill_dirs, cfg)


def write_skills_index(index: dict[str, Any], output_dir: str) -> None:
    _write_skills_index(index, output_dir)


def load_skills_index_from_dir(catalog_dir: str) -> dict[str, Any]:
    return _load_skills_index_from_dir(catalog_dir)


def skills_index_from_decomposed_dir(dir_path: str) -> dict[str, Any]:
    return _skills_index_from_decomposed_dir(dir_path)


def repair_skill_chunks(
    entry_dir: str,
    doc_id: str,
    *,
    config: PageIndexConfigInput | None = None,
) -> None:
    cfg = _config_dict(config)
    _repair_skill_chunks(entry_dir, doc_id, cfg)


def md_to_tree(
    markdown_content: str,
    source_path: str,
    *,
    config: PageIndexConfigInput | None = None,
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


def get_skill_line_content(
    index: dict[str, Any],
    doc_id: str,
    *,
    line_num_specs: list[str] | None = None,
    node_id_specs: list[str] | None = None,
    chunk_id_specs: list[str] | None = None,
) -> list[dict[str, Any]]:
    return _get_skill_line_content(
        index,
        doc_id,
        line_num_specs=line_num_specs,
        node_id_specs=node_id_specs,
        chunk_id_specs=chunk_id_specs,
    )


def get_skill_content_retrieve_result(
    index: dict[str, Any],
    doc_id: str,
    *,
    line_num_specs: list[str] | None = None,
    node_id_specs: list[str] | None = None,
    chunk_id_specs: list[str] | None = None,
    options: ReconstructOptions | dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _get_skill_content_retrieve_result(
        index,
        doc_id,
        line_num_specs=line_num_specs,
        node_id_specs=node_id_specs,
        chunk_id_specs=chunk_id_specs,
        options=_reconstruct_options_native(options),
    )


def reconstruct_skill_markdown(
    index: dict[str, Any],
    doc_id: str,
    *,
    line_num_specs: list[str] | None = None,
    node_id_specs: list[str] | None = None,
    chunk_id_specs: list[str] | None = None,
    options: ReconstructOptions | dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _reconstruct_skill_markdown(
        index,
        doc_id,
        line_num_specs=line_num_specs,
        node_id_specs=node_id_specs,
        chunk_id_specs=chunk_id_specs,
        options=_reconstruct_options_native(options),
    )


def write_reconstructed_skill(
    catalog_dir: str,
    index: dict[str, Any],
    doc_id: str,
    *,
    line_num_specs: list[str] | None = None,
    node_id_specs: list[str] | None = None,
    chunk_id_specs: list[str] | None = None,
    options: ReconstructOptions | dict[str, Any] | None = None,
) -> str:
    return _write_reconstructed_skill(
        catalog_dir,
        index,
        doc_id,
        line_num_specs=line_num_specs,
        node_id_specs=node_id_specs,
        chunk_id_specs=chunk_id_specs,
        options=_reconstruct_options_native(options),
    )


def parse_skill_node_ids(spec: str) -> list[int]:
    return _parse_skill_node_ids(spec)


def parse_skill_chunk_ids(spec: str) -> list[int]:
    return _parse_skill_chunk_ids(spec)


class SkillsBuilder:
    def __init__(self, *, memory_only: bool = True, output_dir: str | None = None) -> None:
        self._inner = _SkillsBuilder(memory_only=memory_only, output_dir=output_dir)

    def build_from_dirs(
        self,
        skill_dirs: list[str],
        *,
        config: PageIndexConfigInput | None = None,
    ) -> dict[str, Any]:
        cfg = _config_dict(config)
        return self._inner.build_from_dirs(skill_dirs, cfg)

    def write_catalog(self) -> dict[str, Any]:
        return self._inner.write_catalog()

    def to_skills_index_json(self) -> dict[str, Any]:
        return self._inner.to_skills_index_json()

    def to_skills_dict(self) -> dict[str, Any]:
        return self._inner.to_skills_dict()


def _config_dict(config: PageIndexConfigInput | None) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, PageIndexConfig):
        return config.to_dict()
    if isinstance(config, dict) and _is_snake_case_pageindex_dict(config):
        return config
    if isinstance(config, dict):
        return PageIndexConfig.from_mapping(config).to_dict()
    return config


def _is_snake_case_pageindex_dict(config: dict[str, Any]) -> bool:
    return any(
        key in config
        for key in (
            "if_add_node_id",
            "if_add_node_text",
            "enable_bm25_chunking",
            "bm25_cohesion",
            "chunk_size",
        )
    )


def _reconstruct_options_native(
    options: ReconstructOptions | dict[str, Any] | None,
) -> _ReconstructOptions | None:
    if options is None:
        return None
    if isinstance(options, ReconstructOptions):
        return options.to_native()
    return _ReconstructOptions(
        keep_all_headers=bool(options.get("keep_all_headers", options.get("keepAllHeaders", False))),
    )
