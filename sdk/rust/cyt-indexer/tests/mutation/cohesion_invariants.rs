//! Cohesion chunk invariants that fail when gap-fill or tail-extension logic is mutated.

use cyt_indexer::{Bm25CohesionChunker, Bm25CohesionConfig, WindowMode, testing_concat_chunks};

#[test]
fn last_chunk_extends_to_source_end() -> Result<(), String> {
    let mut text =
        String::from("Alpha sentence one here. Beta sentence two follows. Gamma third topic now. ");
    for i in 0..12 {
        use std::fmt::Write;
        let _ = write!(
            text,
            "Topic {i} sentence with extra padding words for cohesion. "
        );
    }
    text.push_str("Delta finance detail tail end marker.");
    let cfg = Bm25CohesionConfig {
        chunk_size: 25,
        similarity_window: 6,
        skip_window: 0,
        minimum_words: 0,
        minimum_sentences: 0,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(&text);
    assert!(chunks.len() > 1);
    let last = chunks.last().ok_or("empty chunk list")?;
    assert_eq!(last.end_index, text.len());
    assert_eq!(last.text, &text[last.start_index..]);
    assert_eq!(testing_concat_chunks(&chunks), text);
    Ok(())
}

#[test]
fn inter_chunk_gaps_are_closed_without_dropping_bytes() -> Result<(), String> {
    let text = "## Core Tools\n\n| Tool | Purpose |\n|------|---------|\n| `ctx_read(path)` | Read file with compression |\n| `ctx_search(pattern)` | Search code with compressed results |\n| `ctx_shell(command)` | Run shell with compressed output |";
    let cfg = Bm25CohesionConfig {
        chunk_size: 35,
        similarity_window: 8,
        skip_window: 0,
        minimum_words: 0,
        minimum_sentences: 0,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(text);
    for window in chunks.windows(2) {
        assert_eq!(
            window[0].end_index, window[1].start_index,
            "adjacent chunks must meet without holes"
        );
    }
    assert_eq!(testing_concat_chunks(&chunks), text);
    Ok(())
}
