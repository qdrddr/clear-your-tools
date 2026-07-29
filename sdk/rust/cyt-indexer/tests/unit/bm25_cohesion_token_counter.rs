use cyt_indexer::bm25_cohesion::token_counter::TiktokenCounter;
use cyt_indexer::bm25_cohesion::{TokenCounter, approximate_token_count};

#[test]
fn approximate_matches_legacy_formula() {
    assert_eq!(approximate_token_count(""), 1);
    assert_eq!(approximate_token_count("hello"), 3);
}

#[test]
fn tiktoken_counts_nonzero() {
    let counter = TiktokenCounter;
    assert!(counter.count("hello world") >= 1);
}
