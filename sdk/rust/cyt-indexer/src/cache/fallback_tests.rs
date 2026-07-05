//! Cache fallback and disk roundtrip tests.

#![allow(clippy::unwrap_used)]

use std::path::PathBuf;
use std::sync::{LazyLock, Mutex};

use serde_json::json;

use crate::cache::config::{configure_memory_cache, memory_cache_config};
use crate::cache::{
    CachePolicy, CacheStatus, build_or_open_bm25_index, disk_available,
    ensure_tool_catalog_from_entries, tool_definition_content_hash,
};

static DISK_CACHE_TEST_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));

struct DiskCacheTestGuard {
    _lock: std::sync::MutexGuard<'static, ()>,
    prev_async_disk: bool,
}

impl DiskCacheTestGuard {
    fn new() -> Self {
        let lock = DISK_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let prev_async_disk = memory_cache_config().async_disk_writes;
        configure_memory_cache(&json!({ "async_disk_writes": false }));
        Self {
            _lock: lock,
            prev_async_disk,
        }
    }
}

impl Drop for DiskCacheTestGuard {
    fn drop(&mut self) {
        configure_memory_cache(&json!({ "async_disk_writes": self.prev_async_disk }));
    }
}

fn disk_cache_test_guard() -> DiskCacheTestGuard {
    DiskCacheTestGuard::new()
}

fn sample_tool_entry(name: &str) -> serde_json::Value {
    json!({
        "id": name,
        "server": "",
        "tool": name,
        "summary": "Read a file",
        "full_schema": {
            "id": name,
            "name": name,
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {}}
        }
    })
}

#[test]
fn disk_available_false_for_missing_home_subpath() {
    let path = PathBuf::from("/nonexistent-root/cyt-cache-test");
    assert!(!disk_available(&path));
}

#[test]
fn bm25_index_memory_fallback_when_force_memory() {
    let corpus = ["alpha beta", "gamma delta"];
    let result =
        build_or_open_bm25_index(&corpus, "test-fingerprint", CachePolicy::ForceMemory).unwrap();
    assert!(!result.disk_backed);
    assert_eq!(result.cache_status, CacheStatus::MemoryFallback);
}

#[test]
fn bm25_index_builds_in_memory_for_empty_corpus() {
    let result = build_or_open_bm25_index(&[], "empty", CachePolicy::Auto).unwrap();
    assert!(!result.disk_backed);
    assert_eq!(result.cache_status, CacheStatus::MemoryFallback);
}

#[test]
fn force_memory_env_disables_disk() {
    let _guard = disk_cache_test_guard();
    unsafe {
        std::env::set_var("CYT_CACHE_FORCE_MEMORY", "1");
    }
    let tmp = std::env::temp_dir().join(format!("cyt-cache-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&tmp);
    assert!(!disk_available(&tmp));
    let _ = std::fs::remove_dir_all(&tmp);
    unsafe {
        std::env::remove_var("CYT_CACHE_FORCE_MEMORY");
    }
}

#[test]
fn tool_catalog_disk_hit_skips_rebuild() {
    let _guard = disk_cache_test_guard();
    let tmp = std::env::temp_dir().join(format!("cyt-tools-cache-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    let entry = sample_tool_entry("read_file");
    let content_hash = tool_definition_content_hash(&entry["full_schema"]);
    let per_tool_dir = tmp.join("entries").join(&content_hash);
    let enums: Vec<serde_json::Value> = vec![];

    let first = ensure_tool_catalog_from_entries(
        std::slice::from_ref(&entry),
        &enums,
        "",
        &tmp,
        CachePolicy::Auto,
    )
    .unwrap();
    assert!(first.disk_backed);
    assert_eq!(first.cache_status, CacheStatus::Miss);
    assert!(per_tool_dir.join("schemas").is_dir());
    assert!(!per_tool_dir.join("manifest.json").exists());

    let second = ensure_tool_catalog_from_entries(
        std::slice::from_ref(&entry),
        &enums,
        "",
        &tmp,
        CachePolicy::Auto,
    )
    .unwrap();
    assert!(second.disk_backed);
    assert_eq!(second.cache_status, CacheStatus::Hit);
    assert!(second.data.catalog.get("json").is_some());

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn shared_tool_reuses_per_tool_cache_dir() {
    let _guard = disk_cache_test_guard();
    let tmp = std::env::temp_dir().join(format!("cyt-tools-cache-shared-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    let entry = sample_tool_entry("shared_tool");
    let content_hash = tool_definition_content_hash(&entry["full_schema"]);
    let per_tool_dir = tmp.join("entries").join(&content_hash);
    let enums: Vec<serde_json::Value> = vec![];

    ensure_tool_catalog_from_entries(
        std::slice::from_ref(&entry),
        &enums,
        "",
        &tmp,
        CachePolicy::Auto,
    )
    .unwrap();

    let other_catalog = vec![entry, sample_tool_entry("other_tool")];
    let result =
        ensure_tool_catalog_from_entries(&other_catalog, &enums, "", &tmp, CachePolicy::Auto)
            .unwrap();
    assert_eq!(result.cache_status, CacheStatus::Miss);
    assert!(per_tool_dir.join("schemas").is_dir());
    assert_eq!(result.data.index.tools.len(), 2);

    let _ = std::fs::remove_dir_all(&tmp);
}
