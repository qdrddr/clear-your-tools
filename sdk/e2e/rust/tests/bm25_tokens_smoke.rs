//! Smoke tests for tokens, BM25 search, and cohesion chunking.

use std::fs;
use std::path::PathBuf;

use cyt_indexer::{
    Bm25CohesionChunker, Bm25CohesionConfig, WindowMode, count_tokens, score_catalog_in_place,
    ScoreCatalogOptions,
};
use serde_json::Value;

fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join(name)
}

#[test]
fn count_tokens_smoke() -> Result<(), String> {
    let n = count_tokens("hello world")?;
    assert!(n >= 1);
    Ok(())
}

#[test]
fn bm25_score_catalog_smoke() -> Result<(), String> {
    let raw = fs::read_to_string(fixture_path("bm25_catalog.json")).map_err(|e| e.to_string())?;
    let mut data: Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    score_catalog_in_place(
        &mut data,
        "read files disk",
        &ScoreCatalogOptions::default(),
    )?;
    let first = data["json"][0]["score"]
        .as_str()
        .ok_or("missing score")?
        .parse::<f64>()
        .map_err(|e| e.to_string())?;
    let second = data["json"][1]["score"]
        .as_str()
        .ok_or("missing score")?
        .parse::<f64>()
        .map_err(|e| e.to_string())?;
    assert!(first > second);
    Ok(())
}

#[test]
fn bm25_cohesion_chunk_smoke() -> Result<(), String> {
    let sample = fs::read_to_string(fixture_path("cohesion_sample.md")).map_err(|e| e.to_string())?;
    let cfg_raw =
        fs::read_to_string(fixture_path("cohesion_config.json")).map_err(|e| e.to_string())?;
    let mut cfg = Bm25CohesionConfig::from_partial(
        &serde_json::from_str(&cfg_raw).map_err(|e| e.to_string())?,
    );
    cfg.apply_mode_defaults(WindowMode::Word);
    cfg.validate()?;
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(&sample);
    assert!(!chunks.is_empty());
    let recompiled: String = chunks.iter().map(|c| c.text.as_str()).collect();
    assert_eq!(recompiled, sample);
    Ok(())
}
