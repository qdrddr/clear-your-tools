"""Skills pageindex — re-exports cyt-indexer-sdk with app config helpers."""

from __future__ import annotations

from typing import Any

from cyt_indexer import (
    Bm25CohesionConfig,
    PageIndexConfig,
    PageIndexConfigInput,
    ReconstructOptions,
    SkillsBuilder,
    bm25_cohesion_chunk,
    build_skills_index,
    cohesion_config_dict,
    default_bm25_cohesion_config,
    default_page_index_config,
    get_skill_content_retrieve_result,
    get_skill_document,
    get_skill_line_content,
    get_skill_line_content_from_spec,
    get_skill_structure,
    load_skills_index_from_dir,
    md_to_tree,
    page_index_config_from_mapping,
    page_index_config_without_chunking,
    parse_skill_chunk_ids,
    parse_skill_node_ids,
    reconstruct_skill_markdown,
    repair_skill_chunks,
    skills_index_from_decomposed_dir,
    write_reconstructed_skill,
    write_skills_index,
)

__all__ = [
    "Bm25CohesionConfig",
    "PageIndexConfig",
    "PageIndexConfigInput",
    "ReconstructOptions",
    "SkillsBuilder",
    "bm25_cohesion_chunk",
    "build_skills_index",
    "cohesion_config_dict",
    "default_bm25_cohesion_config",
    "default_page_index_config",
    "get_skill_content_retrieve_result",
    "get_skill_document",
    "get_skill_line_content",
    "get_skill_line_content_from_spec",
    "get_skill_structure",
    "load_skills_index_from_dir",
    "md_to_tree",
    "page_index_config_from_app",
    "page_index_config_from_mapping",
    "page_index_config_without_chunking",
    "parse_skill_chunk_ids",
    "parse_skill_node_ids",
    "reconstruct_skill_markdown",
    "repair_skill_chunks",
    "skills_index_from_decomposed_dir",
    "write_reconstructed_skill",
    "write_skills_index",
]


def page_index_config_from_app(skills_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build pageindex config dict from app ``skills`` config section.

    Reads ``skills_config["pageindex"]`` when present. Partial keys merge with
    cyt-indexer SDK defaults in Rust (``PageIndexConfig::from_value``).
    """
    if not skills_config:
        return None
    pageindex = skills_config.get("pageindex")
    if pageindex is None:
        return None
    if isinstance(pageindex, dict):
        return page_index_config_from_mapping(pageindex)
    return None
