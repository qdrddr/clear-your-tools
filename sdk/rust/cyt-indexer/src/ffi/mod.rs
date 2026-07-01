//! C FFI bindings for cyt-indexer.
//!
//! All exported functions use `cyt_*` naming and C-style return codes.
//! JSON outputs are written to `char**` out parameters; free with [`cyt_free_string`].
#![allow(unsafe_op_in_unsafe_fn)]

mod bm25;
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

pub use error::{
    CYT_ERR_ALLOC, CYT_ERR_INVALID_ARG, CYT_ERR_INVALID_HANDLE, CYT_ERR_INVALID_UTF8, CYT_ERR_IO,
    CYT_ERR_JSON, CYT_ERR_NULL_PTR, CYT_ERR_PANIC, CYT_OK, cyt_clear_error, cyt_get_last_error,
};
pub use memory::{cyt_free_string, cyt_get_version};

pub use catalog::{cyt_build_catalog_index, cyt_catalog_tool_count};

// Re-export opaque handle types for cbindgen.
pub use catalog_io::CytCatalogBuilder;
pub use pageindex::CytSkillsBuilder;
pub use retrieve::CytDecomposedCatalog;
