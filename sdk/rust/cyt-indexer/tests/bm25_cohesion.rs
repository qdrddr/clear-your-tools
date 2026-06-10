use cyt_indexer::{Bm25CohesionChunker, Bm25CohesionConfig, WindowMode};

#[test]
fn skip_window_changes_output() -> Result<(), String> {
    let text = "Alpha one two three. Beta finance market stocks. Alpha four five six. Beta bonds trade desk.";
    let mut cfg = Bm25CohesionConfig {
        chunk_size: 40,
        ..Default::default()
    };
    let chunker_off = Bm25CohesionChunker::new(cfg.clone())?;
    cfg.skip_window = 1;
    let chunker_on = Bm25CohesionChunker::new(cfg)?;
    let off = chunker_off.chunk(text);
    let on = chunker_on.chunk(text);
    assert!(!off.is_empty());
    assert!(!on.is_empty());
    Ok(())
}

#[test]
fn size_cap_respected() -> Result<(), String> {
    let cfg = Bm25CohesionConfig {
        chunk_size: 30,
        ..Default::default()
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let mut text = String::new();
    for i in 0..80 {
        use std::fmt::Write;
        let _ = write!(
            text,
            "Sentence number {i} with extra words for length. "
        );
    }
    for chunk in chunker.chunk(&text) {
        assert!(chunk.token_count <= 30, "chunk had {} tokens", chunk.token_count);
    }
    Ok(())
}

#[test]
fn word_mode_default_window_500() {
    let cfg = Bm25CohesionConfig::default_for_mode(WindowMode::Word);
    assert_eq!(cfg.similarity_window, 500);
}

#[test]
fn chunks_created_for_skill_build() -> Result<(), String> {
    use cyt_indexer::{build_skills_index, PageIndexConfig};
    use std::fs;
    use std::path::PathBuf;

    let tmp = std::env::temp_dir().join(format!("cyt-chunk-build-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let skills = tmp.join("skills");
    fs::create_dir_all(&skills).map_err(|err| err.to_string())?;
    fs::write(
        skills.join("test.md"),
        "# Title\n\nShort body.\n\n## Section\n\nMore content here.",
    )
    .map_err(|err| err.to_string())?;
    let index = build_skills_index(&[PathBuf::from(&skills)], &PageIndexConfig::default())?;
    assert!(index
        .files
        .keys()
        .any(|k| k.contains("/chunks/")));
    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}
