//! C FFI bindings for cyt-indexer.
//!
//! All exported functions use `cyt_*` naming and C-style return codes.
//! JSON outputs are written to `char**` out parameters; free with [`cyt_free_string`].
#![allow(unsafe_op_in_unsafe_fn)]

mod bm25;
mod bm25_search;
mod cache;
mod catalog;
mod catalog_io;
mod documents;
mod error;
mod json_util;
mod memory;
mod pageindex;
mod paths;
mod policies;
mod retrieve;
mod runtime;
mod tokens;

pub use error::{
    CYT_ERR_ALLOC, CYT_ERR_INVALID_ARG, CYT_ERR_INVALID_HANDLE, CYT_ERR_INVALID_UTF8, CYT_ERR_IO,
    CYT_ERR_JSON, CYT_ERR_NULL_PTR, CYT_ERR_PANIC, CYT_OK, cyt_clear_error, cyt_get_last_error,
};
pub use memory::{cyt_free_string, cyt_get_version};

pub use bm25_search::{
    cyt_batch_reconstruct_skill_matches, cyt_bm25_catalog_fingerprint, cyt_bm25_frontmatter_gate,
    cyt_bm25_score_catalog, cyt_bm25_search_skill_chunks, cyt_configure_bm25_defaults,
    cyt_exp_similarity, cyt_greedy_select_skill_items,
};
pub use cache::{
    cyt_configure_memory_cache, cyt_ensure_skills_registry, cyt_ensure_tool_catalog,
    cyt_ensure_tool_catalog_from_entries, cyt_tools_catalog_content_hash,
};
pub use catalog::{cyt_build_catalog_index, cyt_catalog_tool_count};
pub use policies::cyt_classify_optional_chunks_batch;
pub use tokens::{
    cyt_configure_tokenizer_defaults, cyt_count_json_tokens, cyt_count_tokens,
    cyt_count_tokens_batch,
};

// Re-export opaque handle types for cbindgen.
pub use catalog_io::CytCatalogBuilder;
pub use pageindex::CytSkillsBuilder;
pub use retrieve::CytDecomposedCatalog;
