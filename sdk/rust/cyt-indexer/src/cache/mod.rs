//! Shared cache module root.

mod bm25_index;
mod lock;
mod manifest;
mod skills_registry;
mod tools_catalog;

#[cfg(test)]
mod fallback_tests;

pub use bm25_index::{Bm25IndexHandle, build_or_open_bm25_index};
pub use manifest::CacheStatus;
pub use skills_registry::{SkillEntryRef, ensure_skills_registry};
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
        && let Some(home) = std::env::var_os("HOME").map(PathBuf::from)
    {
        return home.join(s.trim_start_matches("~/"));
    }
    path.to_path_buf()
}
