//! Manual BM25 smoke harness — score shared e2e catalog against a query.
//!
//! ```bash
//! cargo test -p cyt-indexer --features testing --test qa_bm25_scratch -- --nocapture -- "read files from disk"
//! cargo test -p cyt-indexer --features testing --test qa_bm25_scratch -- --nocapture
//! ```
#![allow(clippy::panic, clippy::expect_used, clippy::unwrap_used)]

use std::fs;
use std::path::PathBuf;

use cyt_indexer::{ScoreCatalogOptions, score_catalog_in_place};
use serde_json::Value;

const DEFAULT_QUERY: &str = "read files from disk";

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../e2e/fixtures")
}

fn score_entry(entry: &Value) -> f64 {
    entry
        .get("score")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse().ok())
        .or_else(|| entry.get("score").and_then(serde_json::Value::as_f64))
        .unwrap_or(0.0)
}

fn main() {
    let query = std::env::args()
        .skip_while(|arg| arg != "--")
        .nth(1)
        .unwrap_or_else(|| DEFAULT_QUERY.to_string());
    let fixture_path = fixture_root().join("bm25_catalog.json");
    let raw = fs::read_to_string(&fixture_path)
        .unwrap_or_else(|err| panic!("read {}: {err}", fixture_path.display()));
    let mut catalog: Value = serde_json::from_str(&raw).expect("parse bm25_catalog.json");
    score_catalog_in_place(&mut catalog, &query, &ScoreCatalogOptions::default())
        .expect("bm25 score");
    let mut ranked: Vec<(String, f64)> = catalog
        .get("json")
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|entry| {
            let path = entry.get("file_path")?.as_str()?.to_string();
            Some((path, score_entry(entry)))
        })
        .collect();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    println!("query: {query}");
    for (path, score) in ranked {
        println!("{score:.6}\t{path}");
    }
}
