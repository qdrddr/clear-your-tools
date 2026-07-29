use cyt_indexer::cache::MemoryCacheConfig;
use serde_json::json;

#[test]
fn apply_json_nested_lru() {
    let mut cfg = MemoryCacheConfig::default();
    cfg.apply_json(&json!({
        "lazy_registry": false,
        "lru": { "chunk_bodies": 64 }
    }));
    assert!(!cfg.lazy_registry);
    assert_eq!(cfg.lru_chunk_bodies, 64);
}
