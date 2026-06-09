//! Flat re-exports and stable cross-language names (`build_catalog_index`, etc.)
//! intentionally repeat module prefixes where clippy would prefer shorter names.
#![allow(clippy::pub_use, clippy::module_name_repetitions)]

pub mod build;
pub mod catalog_builder;
pub mod catalog_io;
pub mod documents;
pub mod json_util;
pub mod paths;
pub mod policies;
pub mod retrieve;
pub mod runtime_config;
pub mod tool_entries;

#[cfg(feature = "python")]
pub mod python;

#[cfg(feature = "node")]
pub mod node;

pub use build::{
    build_catalog_index, catalog_tool_count, decompose_tool_schema, dedupe_enums, CatalogIndex,
};
pub use catalog_builder::CatalogBuilder;
pub use catalog_io::write_catalog_index;
pub use documents::{
    extract_document_text, extract_json_catalog_document, extract_level_info,
    extract_md_catalog_document,
};
pub use policies::{
    anthropic_tool_is_mcp, anthropic_tool_is_system, catalog_needs_partition,
    catalog_needs_pruned_recompose, chunk_tool_id, direct_root_optional_chunks_for_tool,
    drop_recomposed_tools_with_empty_properties, effective_policy, entries_for_policy,
    filter_recompose_json_entries, full_pass_through, is_decomposed_optional_property_chunk,
    is_decomposed_tool_root_chunk, is_direct_root_optional_property_chunk, is_mcp_optional_chunk,
    is_mcp_root_chunk, is_non_system_chunk, is_non_system_tool_id, is_system_chunk,
    is_system_optional_chunk, is_system_root_chunk, is_system_tool_id, merge_catalog,
    merge_tools_preserving_order, mitigate_empty_optional_properties, needs_empty_optional_mitigation,
    needs_partition, needs_pruned_recompose, optional_chunks_for_tool, optional_leaf_survived_rerank,
    partition_catalog, policy_context_from_values, request_pass_through, restore_mcp_tools,
    restore_system_tools, root_chunk_properties_empty, root_tool_id_from_chunk,
    apply_per_tool_overrides, parse_tool_policy, parse_tool_policy_pair,
    per_tool_policies_from_value,
    split_anthropic_tools, stash_mcp_tools, stash_system_tools, system_required_enum_values,
    system_tools_pass_through, tool_id_had_empty_original_root_properties,
    tool_id_has_empty_decomposed_root, tool_pass_through, tools_for_catalog, uses_pruned_recompose,
    append_description_reinstate_entries, is_description_policy, needs_description_reinstate,
    scoring_policy, PolicyContext, ToolPolicy,
};
pub use paths::{
    collect_enums, configure as configure_paths, get_root_tool_key, snapshot as path_snapshot,
    to_decomposed_key, tool_id_from_decomposed_rel, PathConfig,
};
pub use runtime_config::{
    configure as configure_runtime, decomposed_score, default_mcp_policy, default_system_policy,
    empty_optional_fallback_k, enum_score, rerank_score, snapshot as runtime_snapshot,
    RuntimeConfig,
};
pub use retrieve::{
    apply_description_reinstate_to_data, build_process_groups_options, chunk_survivor_key,
    climb_and_merge, deep_merge, extract_input_files, extract_scores, filter_and_sort_enums,
    group_files, load_catalog_from_dir, parse_json_input, process_groups, removed_chunks,
    resolve_build_catalog, retrieve_core, retrieve_tools_from_catalog, DecomposedCatalog, ProcessGroupsOptions,
    RemovedChunksOptions, RetrieveOptions,
};
pub use tool_entries::{
    anthropic_tool_to_catalog_entry, anthropic_tools_to_catalog_entries, build_catalog_from_tools,
    is_catalog_tool_entry, normalize_tools_for_catalog, prepare_tool_entry, truncate_description,
};
