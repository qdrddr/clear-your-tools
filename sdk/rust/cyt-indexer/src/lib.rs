//! Flat re-exports and stable cross-language names (`build_catalog_index`, etc.)
//! intentionally repeat module prefixes where clippy would prefer shorter names.
#![allow(
    clippy::pub_use,
    clippy::module_name_repetitions,
    clippy::multiple_crate_versions
)]

pub mod analyzer;
pub mod bm25_cohesion;
pub mod bm25_search;
pub mod build;
pub mod catalog_builder;
pub mod catalog_io;
pub mod documents;
pub mod json_util;
pub mod pageindex;
pub mod paths;
pub mod policies;
pub mod retrieve;
pub mod runtime_config;
pub mod skills_builder;
pub mod skills_io;
pub mod tiktoken;
pub mod tool_entries;

#[cfg(feature = "python")]
pub mod python;

#[cfg(feature = "node")]
pub mod node;

#[cfg(feature = "ffi")]
pub mod ffi;

pub use bm25_cohesion::{
    ApproximateTokenCounter, Bm25CohesionChunker, Bm25CohesionConfig, CharacterTokenCounter,
    CohesionChunk, TokenCounter, TokenCounterKind, WindowMode, approximate_token_count,
};
pub use bm25_search::{
    Bm25SearchConfig, CatalogDocument, NormalizeMode, ScoreCatalogOptions, bm25_frontmatter_gate,
    bm25_search_skill_chunks, catalog_fingerprint, collect_catalog_documents,
    configure as configure_bm25_search, exp_similarity, index_path_for_catalog, min_max_normalize,
    normalize_scores, score_catalog_dict, score_catalog_in_place, score_corpus,
    score_query_against_doc, snapshot as bm25_search_snapshot, term_frequencies,
};
pub use build::{
    CatalogIndex, build_catalog_index, catalog_tool_count, decompose_tool_schema, dedupe_enums,
};
pub use catalog_builder::CatalogBuilder;
pub use catalog_io::write_catalog_index;
pub use documents::{
    extract_document_text, extract_json_catalog_document, extract_level_info,
    extract_md_catalog_document,
};
pub use pageindex::{
    MdIndexResult, PageIndexConfig, RETRIEVE_DIR, ReconstructOptions, ReconstructResult,
    SkillDocument, SkillsIndex, build_skills_index,
    get_content_retrieve_result as get_skill_content_retrieve_result,
    get_document as get_skill_document, get_document_structure as get_skill_structure,
    get_line_content as get_skill_line_content,
    get_line_content_from_spec as get_skill_line_content_from_spec, md_to_tree,
    parse_chunk_ids as parse_skill_chunk_ids, parse_line_nums as parse_skill_line_nums,
    parse_node_ids as parse_skill_node_ids, reconstruct_skill_markdown, repair_skill_chunks,
    retrieve_output_rel_path, write_reconstructed_skill,
};
pub use paths::{
    PathConfig, collect_enums, configure as configure_paths, get_root_tool_key,
    is_catalog_decomposed_path, skills_decomposed_prefix, snapshot as path_snapshot,
    to_decomposed_key, to_skills_decomposed_key, tool_id_from_decomposed_rel,
};
pub use policies::{
    PolicyContext, ToolPolicy, anthropic_tool_is_mcp, anthropic_tool_is_system,
    append_description_reinstate_entries, apply_per_tool_overrides, catalog_needs_partition,
    catalog_needs_pruned_recompose, chunk_tool_id, direct_root_optional_chunks_for_tool,
    drop_recomposed_tools_with_empty_properties, effective_policy, entries_for_policy,
    filter_recompose_json_entries, full_pass_through, is_decomposed_optional_property_chunk,
    is_decomposed_tool_root_chunk, is_description_policy, is_direct_root_optional_property_chunk,
    is_mcp_optional_chunk, is_mcp_root_chunk, is_non_system_chunk, is_non_system_tool_id,
    is_system_chunk, is_system_optional_chunk, is_system_root_chunk, is_system_tool_id,
    merge_catalog, merge_tools_preserving_order, mitigate_empty_optional_properties,
    needs_description_reinstate, needs_empty_optional_mitigation, needs_partition,
    needs_pruned_recompose, optional_chunks_for_tool, optional_leaf_survived_rerank,
    parse_tool_policy, parse_tool_policy_pair, partition_catalog, per_tool_policies_from_value,
    policy_context_from_values, request_pass_through, restore_mcp_tools, restore_system_tools,
    root_chunk_properties_empty, root_tool_id_from_chunk, scoring_policy, split_anthropic_tools,
    stash_mcp_tools, stash_system_tools, system_required_enum_values, system_tools_pass_through,
    tool_id_had_empty_original_root_properties, tool_id_has_empty_decomposed_root,
    tool_pass_through, tools_for_catalog, uses_pruned_recompose,
};
pub use retrieve::{
    DecomposedCatalog, ProcessGroupsOptions, RemovedChunksOptions, RetrieveOptions,
    apply_description_reinstate_to_data, build_process_groups_options, chunk_survivor_key,
    climb_and_merge, deep_merge, extract_input_files, extract_scores, filter_and_sort_enums,
    group_files, load_catalog_from_dir, parse_json_input, process_groups, removed_chunks,
    resolve_build_catalog, retrieve_core, retrieve_tools_from_catalog,
};
pub use runtime_config::{
    RuntimeConfig, configure as configure_runtime, decomposed_score, default_mcp_policy,
    default_system_policy, empty_optional_fallback_k, enum_score, rerank_score,
    snapshot as runtime_snapshot,
};
pub use skills_builder::SkillsBuilder;
pub use skills_io::{
    load_decomposed_files_for_index, load_skills_index_from_dir, skills_index_from_decomposed_dir,
    write_skills_index,
};
pub use tiktoken::{
    configure as configure_tiktoken, count_json_tokens, count_tokens, count_tokens_or_min,
    snapshot as tiktoken_snapshot,
};
pub use tool_entries::{
    anthropic_tool_to_catalog_entry, anthropic_tools_to_catalog_entries, build_catalog_from_tools,
    is_catalog_tool_entry, normalize_tools_for_catalog, prepare_tool_entry, truncate_description,
};
