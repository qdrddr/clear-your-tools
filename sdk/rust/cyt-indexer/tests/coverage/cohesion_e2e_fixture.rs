//! Shared e2e cohesion fixture — exercises chunker + token counter stack for coverage.

use std::fs;
use std::path::PathBuf;

use cyt_indexer::{
    Bm25CohesionChunker, Bm25CohesionConfig, TokenCounterKind, WindowMode, testing_concat_chunks,
};

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../e2e/fixtures")
}

#[test]
fn cohesion_sample_md_concat_invariant() -> Result<(), String> {
    let text = fs::read_to_string(fixture_root().join("cohesion_sample.md"))
        .map_err(|err| err.to_string())?;
    // File is short — use config-sized window so one chunk preserves the full fixture.
    let cfg = Bm25CohesionConfig {
        chunk_size: 2048,
        similarity_window: 10,
        skip_window: 0,
        token_counter: TokenCounterKind::Approximate,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(&text);
    assert!(!chunks.is_empty());
    assert_eq!(testing_concat_chunks(&chunks), text);
    for chunk in &chunks {
        assert_eq!(&text[chunk.start_index..chunk.end_index], chunk.text);
    }
    Ok(())
}

#[test]
fn cohesion_config_json_word_mode_runs() -> Result<(), String> {
    let raw = fs::read_to_string(fixture_root().join("cohesion_config.json"))
        .map_err(|err| err.to_string())?;
    let parsed: serde_json::Value = serde_json::from_str(&raw).map_err(|err| err.to_string())?;
    let chunk_size = usize::try_from(
        parsed
            .get("chunk_size")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(2048),
    )
    .map_err(|_| "chunk_size exceeds usize".to_string())?;
    let similarity_window = usize::try_from(
        parsed
            .get("similarity_window")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(10),
    )
    .map_err(|_| "similarity_window exceeds usize".to_string())?;
    let cfg = Bm25CohesionConfig {
        chunk_size,
        similarity_window,
        skip_window: 0,
        token_counter: TokenCounterKind::Tiktoken,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let text = fs::read_to_string(fixture_root().join("cohesion_sample.md"))
        .map_err(|err| err.to_string())?;
    let chunks = chunker.chunk(&text);
    assert_eq!(testing_concat_chunks(&chunks), text);
    Ok(())
}
