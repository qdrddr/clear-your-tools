//! Process-wide hot LRU caches for frequently read cache artifacts.

use std::path::Path;
use std::sync::{LazyLock, Mutex};

use serde_json::Value;

use crate::build::CatalogIndex;
use crate::pageindex::SkillsIndex;

use super::config::memory_cache_config;
use super::lru::LruCache;

static HOT: LazyLock<Mutex<HotCaches>> = LazyLock::new(|| Mutex::new(HotCaches::new()));

struct HotCaches {
    chunk_bodies: LruCache<String, String>,
    merged_documents: LruCache<String, Value>,
    skills_indices: LruCache<String, SkillsIndex>,
    tantivy_handles: LruCache<String, super::Bm25IndexHandle>,
    tool_catalogs: LruCache<String, CatalogIndex>,
}

impl HotCaches {
    fn new() -> Self {
        let cfg = memory_cache_config();
        Self {
            chunk_bodies: LruCache::new(cfg.lru_chunk_bodies),
            merged_documents: LruCache::new(cfg.lru_merged_documents),
            skills_indices: LruCache::new(cfg.lru_skills_index),
            tantivy_handles: LruCache::new(cfg.lru_tantivy_indexes),
            tool_catalogs: LruCache::new(cfg.lru_tool_catalogs),
        }
    }

    fn refresh_capacities(&mut self) {
        let cfg = memory_cache_config();
        self.chunk_bodies = LruCache::new(cfg.lru_chunk_bodies);
        self.merged_documents = LruCache::new(cfg.lru_merged_documents);
        self.skills_indices = LruCache::new(cfg.lru_skills_index);
        self.tantivy_handles = LruCache::new(cfg.lru_tantivy_indexes);
        self.tool_catalogs = LruCache::new(cfg.lru_tool_catalogs);
    }
}

fn with_hot<F, R>(f: F) -> R
where
    F: FnOnce(&mut HotCaches) -> R,
{
    let mut guard = HOT
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    f(&mut guard)
}

/// Rebuild LRU stores after config changes.
pub fn reset_hot_caches() {
    with_hot(HotCaches::refresh_capacities);
}

fn merged_doc_key(entry_dir: &Path, chunk_variant: Option<&Path>) -> String {
    format!(
        "{}|{}",
        entry_dir.display(),
        chunk_variant
            .map(|p| p.display().to_string())
            .unwrap_or_default()
    )
}

fn skills_index_key(entry_dir: &Path, doc_id: &str, chunk_variant: Option<&Path>) -> String {
    format!(
        "{}|{}|{}",
        entry_dir.display(),
        doc_id,
        chunk_variant
            .map(|p| p.display().to_string())
            .unwrap_or_default()
    )
}

/// Read chunk markdown with LRU caching.
///
/// # Errors
///
/// Returns an error when the chunk file cannot be read.
pub fn read_chunk_body(path: &Path) -> Result<String, String> {
    let key = path.display().to_string();
    if let Some(cached) = with_hot(|hot| hot.chunk_bodies.get_cloned(&key)) {
        return Ok(cached);
    }
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    with_hot(|hot| {
        hot.chunk_bodies.insert(key, content.clone());
    });
    Ok(content)
}

/// Store or fetch a merged skill document JSON value.
#[must_use]
pub fn store_merged_document(
    entry_dir: &Path,
    chunk_variant: Option<&Path>,
    document: Value,
) -> Value {
    let key = merged_doc_key(entry_dir, chunk_variant);
    with_hot(|hot| {
        hot.merged_documents.insert(key, document.clone());
    });
    document
}

#[must_use]
pub fn get_merged_document(entry_dir: &Path, chunk_variant: Option<&Path>) -> Option<Value> {
    let key = merged_doc_key(entry_dir, chunk_variant);
    with_hot(|hot| hot.merged_documents.get_cloned(&key))
}

/// Store a fully loaded skills index for later reconstruct/search calls.
pub fn store_skills_index(
    entry_dir: &Path,
    doc_id: &str,
    chunk_variant: Option<&Path>,
    index: SkillsIndex,
) {
    let key = skills_index_key(entry_dir, doc_id, chunk_variant);
    with_hot(|hot| {
        hot.skills_indices.insert(key, index);
    });
}

#[must_use]
pub fn get_skills_index(
    entry_dir: &Path,
    doc_id: &str,
    chunk_variant: Option<&Path>,
) -> Option<SkillsIndex> {
    let key = skills_index_key(entry_dir, doc_id, chunk_variant);
    with_hot(|hot| hot.skills_indices.get_cloned(&key))
}

pub fn store_tantivy_handle(fingerprint: &str, handle: super::Bm25IndexHandle) {
    with_hot(|hot| {
        hot.tantivy_handles.insert(fingerprint.to_string(), handle);
    });
}

#[must_use]
pub fn get_tantivy_handle(fingerprint: &str) -> Option<super::Bm25IndexHandle> {
    let key = fingerprint.to_string();
    with_hot(|hot| hot.tantivy_handles.get_cloned(&key))
}

pub fn store_tool_catalog(content_hash: &str, index: CatalogIndex) {
    with_hot(|hot| {
        hot.tool_catalogs.insert(content_hash.to_string(), index);
    });
}

#[must_use]
pub fn get_tool_catalog(content_hash: &str) -> Option<CatalogIndex> {
    let key = content_hash.to_string();
    with_hot(|hot| hot.tool_catalogs.get_cloned(&key))
}

#[cfg(test)]
pub fn hot_cache_len(kind: &str) -> usize {
    with_hot(|hot| match kind {
        "chunk_bodies" => hot.chunk_bodies.len(),
        "merged_documents" => hot.merged_documents.len(),
        "skills_indices" => hot.skills_indices.len(),
        "tantivy_handles" => hot.tantivy_handles.len(),
        "tool_catalogs" => hot.tool_catalogs.len(),
        _ => 0,
    })
}
