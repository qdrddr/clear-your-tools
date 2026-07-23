//! Canonical manifest of cyt-indexer C FFI exports.
//!
//! Used by binding parity checks and documentation generation. Macro-generated symbols
//! that cbindgen cannot see are declared in [`cbindgen_stubs.rs`] for header emission.

/// One exported `cyt_*` C symbol.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FfiExport {
    pub name: &'static str,
    pub category: &'static str,
}

/// All exported FFI symbols grouped by module category.
pub const EXPORTS: &[FfiExport] = &[
    // core / memory
    export("cyt_clear_error", "core"),
    export("cyt_free_string", "core"),
    export("cyt_get_last_error", "core"),
    export("cyt_get_version", "core"),
    // build / catalog
    export("cyt_build_catalog_index", "build"),
    export("cyt_build_catalog_from_tools", "build"),
    export("cyt_catalog_tool_count", "build"),
    export("cyt_anthropic_tools_to_catalog_entries", "build"),
    export("cyt_anthropic_tool_to_catalog_entry", "build"),
    export("cyt_prepare_tool_entry", "build"),
    export("cyt_truncate_description", "build"),
    export("cyt_catalog_index_to_catalog_dict", "build"),
    export("cyt_catalog_index_tool_schema_metadata", "build"),
    export("cyt_catalog_builder_new", "build"),
    export("cyt_catalog_builder_free", "build"),
    export("cyt_catalog_builder_add_tool", "build"),
    export("cyt_catalog_builder_get_tool_info", "build"),
    export("cyt_catalog_builder_build_index", "build"),
    export("cyt_catalog_builder_write_catalog", "build"),
    export("cyt_catalog_builder_to_catalog_dict", "build"),
    export("cyt_write_catalog_index", "build"),
    // tokens
    export("cyt_count_tokens", "tokens"),
    export("cyt_count_json_tokens", "tokens"),
    export("cyt_count_tokens_batch", "tokens"),
    export("cyt_configure_tokenizer_defaults", "tokens"),
    // bm25
    export("cyt_configure_bm25_defaults", "bm25"),
    export("cyt_bm25_catalog_fingerprint", "bm25"),
    export("cyt_bm25_score_catalog", "bm25"),
    export("cyt_bm25_frontmatter_gate", "bm25"),
    export("cyt_bm25_search_skill_chunks", "bm25"),
    export("cyt_batch_reconstruct_skill_matches", "bm25"),
    export("cyt_greedy_select_skill_items", "bm25"),
    export("cyt_exp_similarity", "bm25"),
    export("cyt_bm25_cohesion_default_config", "bm25"),
    export("cyt_bm25_cohesion_chunk", "bm25"),
    // cache
    export("cyt_configure_memory_cache", "cache"),
    export("cyt_tools_catalog_content_hash", "cache"),
    export("cyt_ensure_tool_catalog", "cache"),
    export("cyt_ensure_tool_catalog_from_entries", "cache"),
    export("cyt_ensure_skills_registry", "cache"),
    // runtime
    export("cyt_configure_runtime_defaults", "runtime"),
    export("cyt_runtime_decomposed_score", "runtime"),
    export("cyt_runtime_enum_score", "runtime"),
    export("cyt_runtime_rerank_score", "runtime"),
    export("cyt_runtime_empty_optional_fallback_k", "runtime"),
    export("cyt_runtime_default_system_policy", "runtime"),
    export("cyt_runtime_default_mcp_policy", "runtime"),
    // paths
    export("cyt_configure_path_constants", "paths"),
    export("cyt_collect_enums", "paths"),
    export("cyt_to_decomposed_key", "paths"),
    export("cyt_tool_id_from_decomposed_rel", "paths"),
    export("cyt_get_root_tool_key", "paths"),
    export("cyt_path_md_ext", "paths"),
    export("cyt_path_json_ext", "paths"),
    export("cyt_path_decomposed_prefix", "paths"),
    export("cyt_path_decomposed_root", "paths"),
    export("cyt_path_catalog_prefix", "paths"),
    export("cyt_path_default_catalog_dir", "paths"),
    export("cyt_path_builder_memory_only", "paths"),
    export("cyt_path_write_catalog_prune", "paths"),
    // policies — context / catalog
    export("cyt_tool_policies", "policies"),
    export("cyt_policy_context_from_values", "policies"),
    export("cyt_effective_policy", "policies"),
    export("cyt_tool_pass_through", "policies"),
    export("cyt_batch_tool_pass_through", "policies"),
    export("cyt_partition_catalog", "policies"),
    export("cyt_merge_catalog", "policies"),
    export("cyt_catalog_needs_partition", "policies"),
    export("cyt_catalog_needs_pruned_recompose", "policies"),
    export("cyt_request_pass_through", "policies"),
    export("cyt_full_pass_through", "policies"),
    export("cyt_needs_partition", "policies"),
    export("cyt_needs_pruned_recompose", "policies"),
    export("cyt_system_tools_pass_through", "policies"),
    export("cyt_mcp_tools_pass_through", "policies"),
    export("cyt_needs_description_reinstate", "policies"),
    // policies — chunk classifiers
    export("cyt_is_decomposed_tool_root_chunk", "policies"),
    export("cyt_is_decomposed_optional_property_chunk", "policies"),
    export("cyt_is_system_chunk", "policies"),
    export("cyt_is_non_system_chunk", "policies"),
    export("cyt_is_system_root_chunk", "policies"),
    export("cyt_is_mcp_root_chunk", "policies"),
    export("cyt_is_system_optional_chunk", "policies"),
    export("cyt_is_mcp_optional_chunk", "policies"),
    export("cyt_is_direct_root_optional_property_chunk", "policies"),
    export("cyt_root_chunk_properties_empty", "policies"),
    export("cyt_classify_optional_chunks_batch", "policies"),
    // policies — stash / restore
    export("cyt_stash_system_tools", "policies"),
    export("cyt_restore_system_tools", "policies"),
    export("cyt_stash_mcp_tools", "policies"),
    export("cyt_restore_mcp_tools", "policies"),
    export("cyt_merge_tools_preserving_order", "policies"),
    export("cyt_split_anthropic_tools", "policies"),
    // policies — recompose / enums
    export("cyt_filter_recompose_json_entries", "policies"),
    export("cyt_mitigate_empty_optional_properties", "policies"),
    export("cyt_append_description_reinstate_entries", "policies"),
    export("cyt_is_description_policy", "policies"),
    export("cyt_scoring_policy", "policies"),
    export(
        "cyt_drop_recomposed_tools_with_empty_properties",
        "policies",
    ),
    export("cyt_root_tool_id_from_chunk", "policies"),
    export("cyt_chunk_tool_id", "policies"),
    export("cyt_is_non_system_tool_id", "policies"),
    export("cyt_is_system_tool_id", "policies"),
    export("cyt_entries_for_policy", "policies"),
    export("cyt_tools_for_catalog", "policies"),
    export("cyt_system_required_enum_values", "policies"),
    export("cyt_mcp_required_enum_values", "policies"),
    export("cyt_required_enum_values_by_tool", "policies"),
    export("cyt_optional_leaf_survived_rerank", "policies"),
    export("cyt_anthropic_tool_is_system", "policies"),
    export("cyt_anthropic_tool_is_mcp", "policies"),
    export("cyt_direct_root_optional_chunks_for_tool", "policies"),
    export("cyt_tool_id_has_empty_decomposed_root", "policies"),
    export("cyt_tool_id_had_empty_original_root_properties", "policies"),
    // pipeline — composite APIs
    export("cyt_prune_catalog_bm25_and_retrieve", "pipeline"),
    export("cyt_recompose_and_retrieve_tools", "pipeline"),
    export("cyt_classify_and_count_catalog", "pipeline"),
    export("cyt_search_skills_and_select", "pipeline"),
    export("cyt_build_skill_node_catalog", "pipeline"),
    export("cyt_coordinate_bm25_prune", "pipeline"),
    // documents
    export("cyt_extract_document_text", "documents"),
    export("cyt_extract_level_info", "documents"),
    export("cyt_extract_json_catalog_document", "documents"),
    export("cyt_extract_md_catalog_document", "documents"),
    // retrieve
    export("cyt_decomposed_catalog_new", "retrieve"),
    export("cyt_decomposed_catalog_free", "retrieve"),
    export("cyt_decomposed_catalog_from_catalog_index", "retrieve"),
    export("cyt_decomposed_catalog_from_catalog_dict", "retrieve"),
    export("cyt_decomposed_catalog_has_json", "retrieve"),
    export("cyt_decomposed_catalog_get_json", "retrieve"),
    export("cyt_load_catalog", "retrieve"),
    export("cyt_retrieve_tools", "retrieve"),
    export("cyt_retrieve_core", "retrieve"),
    export("cyt_chunk_survivor_key", "retrieve"),
    export("cyt_removed_chunks", "retrieve"),
    export("cyt_retrieve_catalog_tool_count", "retrieve"),
    export("cyt_resolve_build_catalog", "retrieve"),
    // pageindex / skills
    export("cyt_build_skills_index", "pageindex"),
    export("cyt_write_skills_index", "pageindex"),
    export("cyt_load_skills_index_from_dir", "pageindex"),
    export("cyt_repair_skill_chunks", "pageindex"),
    export("cyt_skills_index_from_decomposed_dir", "pageindex"),
    export("cyt_md_to_tree", "pageindex"),
    export("cyt_get_skill_document", "pageindex"),
    export("cyt_get_skill_structure", "pageindex"),
    export("cyt_get_skill_line_content_from_spec", "pageindex"),
    export("cyt_get_skill_content_retrieve_result", "pageindex"),
    export("cyt_reconstruct_skill_markdown", "pageindex"),
    export("cyt_write_reconstructed_skill", "pageindex"),
    export("cyt_get_skill_line_content", "pageindex"),
    export("cyt_token_count_from_decomposed_frontmatter", "pageindex"),
    export("cyt_parse_skill_chunk_ids", "pageindex"),
    export("cyt_parse_skill_node_ids", "pageindex"),
    export("cyt_skills_builder_new", "pageindex"),
    export("cyt_skills_builder_free", "pageindex"),
    export("cyt_skills_builder_build_from_dirs", "pageindex"),
    export("cyt_skills_builder_write_catalog", "pageindex"),
    export("cyt_skills_builder_to_skills_index_json", "pageindex"),
    export("cyt_skills_builder_to_skills_dict", "pageindex"),
    export("cyt_reconstruct_options_default", "pageindex"),
    export("cyt_build_page_index_only", "pageindex"),
    export("cyt_build_chunk_variant", "pageindex"),
    export("cyt_page_index_valid", "pageindex"),
    export("cyt_chunk_variant_valid", "pageindex"),
    export("cyt_repair_skill_variant_chunks", "pageindex"),
    export("cyt_load_skills_index_from_entry", "pageindex"),
    export("cyt_load_merged_skill_document_json", "pageindex"),
    export("cyt_finalize_skill_document_json", "pageindex"),
    export("cyt_update_skill_document_source_path", "pageindex"),
];

const fn export(name: &'static str, category: &'static str) -> FfiExport {
    FfiExport { name, category }
}

/// N-API-only exports (Node/TypeScript SDK). Not part of the C FFI surface; listed for parity checks.
pub const NAPI_EXPORTS: &[&str] = &[
    "batchToolPassThrough",
    "pruneCatalogBm25AndRetrieve",
    "recomposeAndRetrieveTools",
    "classifyAndCountCatalog",
    "searchSkillsAndSelect",
    "buildSkillNodeCatalog",
    "coordinateBm25Prune",
    "resolveBuildCatalog",
    "retrieveCatalogToolCount",
    "runtimeDefaultSystemPolicy",
    "runtimeDefaultMcpPolicy",
];

/// Macro-generated symbols documented via cbindgen stubs (not visible to cbindgen in macro expansion).
pub const CBINDGEN_STUB_SYMBOLS: &[&str] = &[
    "cyt_full_pass_through",
    "cyt_needs_description_reinstate",
    "cyt_needs_partition",
    "cyt_needs_pruned_recompose",
    "cyt_system_tools_pass_through",
    "cyt_mcp_tools_pass_through",
    "cyt_tool_pass_through",
    "cyt_is_decomposed_tool_root_chunk",
    "cyt_is_decomposed_optional_property_chunk",
    "cyt_is_system_chunk",
    "cyt_is_non_system_chunk",
    "cyt_is_system_root_chunk",
    "cyt_is_mcp_root_chunk",
    "cyt_is_system_optional_chunk",
    "cyt_is_mcp_optional_chunk",
    "cyt_is_direct_root_optional_property_chunk",
    "cyt_root_chunk_properties_empty",
    "cyt_stash_system_tools",
    "cyt_restore_system_tools",
    "cyt_stash_mcp_tools",
    "cyt_restore_mcp_tools",
    "cyt_path_md_ext",
    "cyt_path_json_ext",
    "cyt_path_decomposed_prefix",
    "cyt_path_decomposed_root",
    "cyt_path_catalog_prefix",
    "cyt_path_default_catalog_dir",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exports_are_unique() {
        let mut seen = std::collections::HashSet::new();
        for exp in EXPORTS {
            assert!(seen.insert(exp.name), "duplicate export: {}", exp.name);
        }
    }

    #[test]
    fn cbindgen_stubs_listed_in_exports() {
        for name in CBINDGEN_STUB_SYMBOLS {
            assert!(
                EXPORTS.iter().any(|e| e.name == *name),
                "stub symbol missing from EXPORTS: {name}"
            );
        }
    }
}
