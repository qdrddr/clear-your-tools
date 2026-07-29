use cyt_indexer::bm25_cohesion::{Bm25CohesionConfig, WindowMode};
use serde_json::json;

#[test]
fn word_mode_defaults_similarity_window_500() {
    let cfg = Bm25CohesionConfig::default_for_mode(WindowMode::Word);
    assert_eq!(cfg.similarity_window, 500);
    assert_eq!(cfg.next_unit_size, 5);
}

#[test]
fn partial_merge_preserves_unset() {
    let cfg = Bm25CohesionConfig::from_partial(&json!({"skip_window": 2}));
    assert_eq!(cfg.skip_window, 2);
    assert_eq!(cfg.similarity_window, 3);
}

#[test]
fn validate_rejects_bad_filter() {
    let cfg = Bm25CohesionConfig {
        filter_polyorder: 5,
        ..Default::default()
    };
    assert!(cfg.validate().is_err());
}
