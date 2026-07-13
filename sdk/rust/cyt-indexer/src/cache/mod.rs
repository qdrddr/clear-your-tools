//! Shared cache module root.

mod bm25_index;
mod config;
mod disk_writer;
mod hot;
mod lock;
mod lru;
mod manifest;
mod materialize;
mod skills_registry;
mod tools_catalog;

#[cfg(test)]
mod fallback_tests;

#[cfg(test)]
mod memory_tests;

#[cfg(test)]
mod test_guard;

pub use bm25_index::{Bm25IndexHandle, build_or_open_bm25_index};
pub use config::{MemoryCacheConfig, configure_memory_cache, memory_cache_config};
pub use hot::{
    get_merged_document, get_skills_index, get_tantivy_handle, get_tool_catalog, read_chunk_body,
    reset_hot_caches, store_merged_document, store_skills_index, store_tantivy_handle,
    store_tool_catalog,
};
pub use manifest::CacheStatus;
pub use materialize::{ensure_entry_materialized, materialize_skill_entry};
#[cfg(any(feature = "ffi", feature = "python", feature = "node"))]
pub(crate) use skills_registry::parse_skill_sources;
pub use skills_registry::{SkillEntryRef, ensure_skills_registry};
#[cfg(any(feature = "ffi", feature = "python", feature = "node", test))]
pub(crate) use skills_registry::{
    ensure_skills_registry_from_specs, parse_skill_source_specs_json,
};
pub use tools_catalog::{
    ToolCatalogHandle, ensure_tool_catalog, ensure_tool_catalog_from_entries,
    tool_definition_content_hash, tools_content_hash,
};

use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CachePolicy {
    Auto,
    ForceMemory,
    ForceDisk,
}

#[derive(Debug, Clone)]
pub struct CacheResult<T> {
    pub data: T,
    pub disk_backed: bool,
    pub cache_status: CacheStatus,
}

/// Return whether *path* is writable (create parents when missing).
#[must_use]
pub fn disk_available(path: &Path) -> bool {
    if std::env::var_os("CYT_CACHE_FORCE_MEMORY").is_some() {
        return false;
    }
    let expanded = expand_tilde(path);
    if expanded.exists() {
        return expanded.is_dir() && is_writable_dir(&expanded);
    }
    if let Some(parent) = expanded.parent() {
        if !parent.exists() && std::fs::create_dir_all(parent).is_err() {
            return false;
        }
        return parent.is_dir() && is_writable_dir(parent);
    }
    false
}

fn is_writable_dir(path: &Path) -> bool {
    let probe = path.join(".cyt_write_probe");
    match std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&probe)
    {
        Ok(_) => {
            let _ = std::fs::remove_file(probe);
            true
        }
        Err(_) => false,
    }
}

#[must_use]
pub fn expand_tilde(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if s.starts_with("~/")
        && let Ok(home) = crate::paths::home_dir()
    {
        return home.join(s.trim_start_matches("~/"));
    }
    path.to_path_buf()
}
