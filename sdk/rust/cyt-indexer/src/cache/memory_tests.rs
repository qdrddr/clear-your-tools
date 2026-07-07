//! In-memory cache layer tests.

#![allow(clippy::unwrap_used)]

use std::path::PathBuf;

use crate::cache::hot::{hot_cache_len, read_chunk_body, store_merged_document};
use crate::cache::lru::LruCache;
use crate::cache::materialize::extract_frontmatter_from_markdown;
use crate::cache::test_guard::CacheConfigTestGuard;
use crate::cache::{CachePolicy, ensure_skills_registry};
use crate::pageindex::PageIndexConfig;
use serde_json::json;

#[test]
fn lru_evicts_oldest_entry() {
    let mut cache = LruCache::new(2);
    cache.insert("a", 1);
    cache.insert("b", 2);
    let _ = cache.get(&"a");
    cache.insert("c", 3);
    assert!(cache.get(&"b").is_none());
    assert_eq!(cache.get(&"a"), Some(&1));
    assert_eq!(cache.get(&"c"), Some(&3));
}

#[test]
fn chunk_body_cache_hits_on_second_read() {
    let tmp = std::env::temp_dir().join(format!("cyt-chunk-cache-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("c0.md");
    std::fs::write(&path, "# Chunk body").unwrap();
    let first = read_chunk_body(&path).unwrap();
    assert_eq!(first, "# Chunk body");
    assert_eq!(hot_cache_len("chunk_bodies"), 1);
    std::fs::write(&path, "# Changed").unwrap();
    let second = read_chunk_body(&path).unwrap();
    assert_eq!(second, "# Chunk body");
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn lazy_registry_defers_full_index() {
    let _guard = CacheConfigTestGuard::with_patch(&json!({ "lazy_registry": true }));
    let tmp = std::env::temp_dir().join(format!("cyt-lazy-reg-{}", std::process::id()));
    let skills = tmp.join("skills");
    let catalog = tmp.join("catalog");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&skills).unwrap();
    std::fs::write(
        skills.join("demo.md"),
        "---\nname: demo\n---\n# Title\n\nBody",
    )
    .unwrap();

    let refs = ensure_skills_registry(
        &[skills.join("demo.md")],
        &catalog,
        &PageIndexConfig::default(),
        "bm25",
        "test-hash",
        CachePolicy::ForceMemory,
    )
    .unwrap();
    assert_eq!(refs.len(), 1);
    assert!(refs[0].lazy_pending);
    assert!(!refs[0].entry_dir.join("nodes/page_index.json").exists());
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn inline_content_skips_disk_source_read() {
    let tmp = std::env::temp_dir().join(format!("cyt-inline-reg-{}", std::process::id()));
    let catalog = tmp.join("catalog");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&catalog).unwrap();

    let source = serde_json::json!([{
        "path": "/virtual/client/skills/demo.md",
        "content": "---\nname: demo\n---\n# Title\n\nInline body",
        "content_sha256": "abc123deadbeef", // pragma: allowlist secret
    }]);
    let specs = crate::cache::parse_skill_source_specs_json(source.as_array().unwrap()).unwrap();
    let refs = crate::cache::ensure_skills_registry_from_specs(
        &specs,
        &catalog,
        &PageIndexConfig::default(),
        "bm25",
        "inline-hash",
        CachePolicy::ForceMemory,
    )
    .unwrap();
    assert_eq!(refs.len(), 1);
    assert_eq!(refs[0].doc_id, "demo");
    assert_eq!(refs[0].source_path, "/virtual/client/skills/demo.md");
    assert_eq!(refs[0].content_sha256, "abc123deadbeef"); // pragma: allowlist secret
    assert!(!refs[0].lazy_pending);
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn merged_document_store_roundtrip() {
    let entry = PathBuf::from("/tmp/example-entry");
    let doc = json!({"id": "demo", "structure": []});
    let stored = store_merged_document(&entry, None, doc);
    assert_eq!(stored["id"], "demo");
}

#[test]
fn frontmatter_extractor() {
    assert_eq!(
        extract_frontmatter_from_markdown("---\nname: x\n---\nbody"),
        Some("name: x".to_string())
    );
}
