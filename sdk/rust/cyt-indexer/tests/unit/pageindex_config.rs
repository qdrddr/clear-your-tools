use cyt_indexer::bm25_cohesion::WindowMode;
use cyt_indexer::pageindex::PageIndexConfig;

#[test]
fn defaults_match_cyt_yaml() {
    let cfg = PageIndexConfig::default();
    assert!(cfg.if_add_node_id);
    assert!(!cfg.if_add_node_text);
    assert!(cfg.enable_bm25_chunking);
    assert!(cfg.bm25_splitting_enabled());
    assert_eq!(cfg.bm25_cohesion.chunk_size, 2048);
}

#[test]
fn from_value_partial_override() {
    let cfg = PageIndexConfig::from_value(&serde_json::json!({"if_add_node_text": true}));
    assert!(cfg.if_add_node_id);
    assert!(cfg.if_add_node_text);
}

#[test]
fn from_value_bm25_nested() {
    let cfg = PageIndexConfig::from_value(&serde_json::json!({
        "bm25_cohesion": {"skip_window": 2, "window_mode": "word"}
    }));
    assert_eq!(cfg.bm25_cohesion.skip_window, 2);
    assert_eq!(cfg.bm25_cohesion.window_mode, WindowMode::Word);
    assert_eq!(cfg.bm25_cohesion.similarity_window, 500);
}

#[test]
fn from_value_ignores_unknown_keys() {
    let cfg = PageIndexConfig::from_value(&serde_json::json!({"if_add_node_summary": "yes"}));
    assert_eq!(
        cfg.if_add_node_id,
        PageIndexConfig::default().if_add_node_id
    );
}

#[test]
fn enable_bm25_chunking_false_disables_splitting_only() {
    let cfg = PageIndexConfig::from_value(&serde_json::json!({"enable_bm25_chunking": false}));
    assert!(!cfg.bm25_splitting_enabled());
    assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
}

#[test]
fn chunk_size_zero_disables_splitting() {
    let cfg = PageIndexConfig::from_value(&serde_json::json!({"chunk_size": 0}));
    assert!(!cfg.bm25_splitting_enabled());
    assert_eq!(cfg.bm25_cohesion.chunk_size, 0);
    assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
}

#[test]
fn without_bm25_chunking_helper() {
    let cfg = PageIndexConfig::without_bm25_chunking();
    assert!(!cfg.bm25_splitting_enabled());
    assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
}
