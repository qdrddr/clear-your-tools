//! Persisted Tantivy BM25 index open/build helpers.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use tantivy::{Index, IndexReader};

use super::hot::{get_tantivy_handle, store_tantivy_handle};
use super::lock::BuildLock;
use super::manifest::CacheStatus;
use super::{CachePolicy, CacheResult, disk_available};
use crate::bm25::index::{build_disk_index, build_ram_index, register_body_analyzer};
use crate::bm25::{expand_index_dir, search_snapshot};

const TANTIVY_DIR: &str = "tantivy";

fn open_disk_index(dir: &Path, _mmap: bool) -> Result<Index, String> {
    let index = Index::open_in_dir(dir).map_err(|e| e.to_string())?;
    register_body_analyzer(&index);
    Ok(index)
}

#[derive(Clone)]
pub struct Bm25IndexHandle {
    index: Arc<Index>,
    reader: IndexReader,
    pub disk_backed: bool,
    pub cache_status: CacheStatus,
}

impl Bm25IndexHandle {
    #[must_use]
    pub const fn reader(&self) -> &IndexReader {
        &self.reader
    }

    #[must_use]
    pub fn index(&self) -> &Index {
        &self.index
    }
}

fn cache_root(fingerprint: &str) -> PathBuf {
    let cfg = search_snapshot();
    expand_index_dir(&cfg.index_dir).join(fingerprint)
}

fn from_index(
    index: Index,
    disk_backed: bool,
    cache_status: CacheStatus,
) -> Result<Bm25IndexHandle, String> {
    let index = Arc::new(index);
    let reader = index.reader().map_err(|e| e.to_string())?;
    Ok(Bm25IndexHandle {
        index,
        reader,
        disk_backed,
        cache_status,
    })
}

/// Build or open a Tantivy index for *corpus*, using disk when available.
///
/// # Errors
///
/// Returns an error when index construction fails without a memory fallback path.
pub fn build_or_open_bm25_index(
    corpus: &[&str],
    fingerprint: &str,
    policy: CachePolicy,
) -> Result<CacheResult<Bm25IndexHandle>, String> {
    if corpus.is_empty() {
        let index = build_ram_index(corpus)?;
        let handle = from_index(index, false, CacheStatus::MemoryFallback)?;
        return Ok(CacheResult {
            data: handle,
            disk_backed: false,
            cache_status: CacheStatus::MemoryFallback,
        });
    }

    if let Some(handle) = get_tantivy_handle(fingerprint) {
        return Ok(CacheResult {
            data: handle.clone(),
            disk_backed: handle.disk_backed,
            cache_status: handle.cache_status,
        });
    }

    let root = cache_root(fingerprint);
    let tantivy_dir = root.join(TANTIVY_DIR);
    let cfg = search_snapshot();

    let try_disk = policy != CachePolicy::ForceMemory && disk_available(&root);
    if try_disk
        && tantivy_dir.is_dir()
        && let Ok(index) = open_disk_index(&tantivy_dir, cfg.mmap)
    {
        let handle = from_index(index, true, CacheStatus::Hit)?;
        store_tantivy_handle(fingerprint, handle.clone());
        return Ok(CacheResult {
            data: handle,
            disk_backed: true,
            cache_status: CacheStatus::Hit,
        });
    }

    if try_disk && let Ok(_lock) = BuildLock::acquire(&root) {
        if tantivy_dir.is_dir()
            && let Ok(index) = open_disk_index(&tantivy_dir, cfg.mmap)
        {
            let handle = from_index(index, true, CacheStatus::Hit)?;
            store_tantivy_handle(fingerprint, handle.clone());
            return Ok(CacheResult {
                data: handle,
                disk_backed: true,
                cache_status: CacheStatus::Hit,
            });
        }
        let index = build_disk_index(corpus, &tantivy_dir)?;
        let handle = from_index(index, true, CacheStatus::Miss)?;
        store_tantivy_handle(fingerprint, handle.clone());
        return Ok(CacheResult {
            data: handle,
            disk_backed: true,
            cache_status: CacheStatus::Miss,
        });
    }

    let index = build_ram_index(corpus)?;
    let handle = from_index(index, false, CacheStatus::MemoryFallback)?;
    store_tantivy_handle(fingerprint, handle.clone());
    Ok(CacheResult {
        data: handle,
        disk_backed: false,
        cache_status: CacheStatus::MemoryFallback,
    })
}
