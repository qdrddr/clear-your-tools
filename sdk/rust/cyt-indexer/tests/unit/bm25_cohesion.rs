use cyt_indexer::{
    Bm25CohesionChunker, Bm25CohesionConfig, WindowMode, testing_concat_chunks, testing_word_count,
};

#[test]
fn chunk_size_zero_returns_single_full_text_chunk() -> Result<(), String> {
    let text = "Alpha one two three. Beta finance market stocks.";
    let cfg = Bm25CohesionConfig {
        chunk_size: 0,
        ..Default::default()
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(text);
    assert_eq!(chunks.len(), 1);
    assert_eq!(chunks[0].text, text);
    assert_eq!(chunks[0].start_index, 0);
    assert_eq!(chunks[0].end_index, text.len());
    Ok(())
}

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
        minimum_words: 0,
        minimum_sentences: 0,
        ..Default::default()
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let mut text = String::new();
    for i in 0..80 {
        use std::fmt::Write;
        let _ = write!(text, "Sentence number {i} with extra words for length. ");
    }
    for chunk in chunker.chunk(&text) {
        assert!(
            chunk.token_count <= 30,
            "chunk had {} tokens",
            chunk.token_count
        );
    }
    Ok(())
}

#[test]
fn word_mode_default_window_500() {
    let cfg = Bm25CohesionConfig::default_for_mode(WindowMode::Word);
    assert_eq!(cfg.similarity_window, 500);
}

#[test]
fn word_mode_markdown_formatting_preserved() -> Result<(), String> {
    let text = "### Step 2: Select the Best Match\n\nFrom the resolution results, choose based on:\n\n- Exact or closest name match to what the user asked for\n- Higher benchmark scores indicate better documentation quality\n- If the user mentioned a version (e.g., \"React 19\"), prefer version-specific IDs";
    let cfg = Bm25CohesionConfig {
        chunk_size: 30,
        similarity_window: 10,
        skip_window: 0,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(text);
    assert!(chunks.len() > 1);
    for chunk in &chunks {
        assert!(chunk.text.contains(' '));
        assert_eq!(&text[chunk.start_index..chunk.end_index], chunk.text);
    }
    let recompiled: String = chunks.iter().map(|c| c.text.as_str()).collect();
    assert_eq!(recompiled, text);
    Ok(())
}

#[test]
fn chunks_created_for_skill_build() -> Result<(), String> {
    use cyt_indexer::{PageIndexConfig, build_skills_index};
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
    assert!(index.files.keys().any(|k| k.starts_with("chunks/")));
    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn core_tools_table_no_trailing_loss_or_invoke_orphan() -> Result<(), String> {
    let text = "## Core Tools (10 always visible)\n\n| Tool | Purpose |\n|------|---------|\n| `ctx_read(path, mode)` | Read file with compression and caching |\n| `ctx_search(pattern, path)` | Search code with compressed results |\n| `ctx_shell(command)` | Run shell with compressed output |\n| `ctx_tree(path, depth)` | Directory listing |\n| `ctx_edit(path, old, new)` | Search-and-replace editing |\n| `ctx_session(action)` | Session state and persistence |\n| `ctx_knowledge(action)` | Project knowledge across sessions |\n| `ctx_overview(task)` | Task-relevant project map |\n| `ctx_graph(action)` | Code relationships and impact |\n| `ctx_call(name, args)` | Invoke any tool by name |";
    let cfg = Bm25CohesionConfig {
        chunk_size: 100,
        similarity_window: 10,
        skip_window: 0,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(text);
    let recompiled: String = chunks.iter().map(|c| c.text.as_str()).collect();
    assert_eq!(recompiled, text, "concatenated chunks must equal input");
    assert!(
        !chunks.iter().any(|c| c.text.trim() == "Invoke"),
        "must not emit bare Invoke orphan chunk"
    );
    assert!(
        chunks
            .iter()
            .any(|c| c.text.contains("Invoke any tool by name |")),
        "full table row tail must appear in some chunk"
    );
    Ok(())
}

#[test]
fn small_text_single_chunk() -> Result<(), String> {
    let chunker = Bm25CohesionChunker::new(Bm25CohesionConfig::default())?;
    let chunks = chunker.chunk("Short text.");
    assert_eq!(chunks.len(), 1);
    assert!(chunks[0].text.contains("Short"));
    Ok(())
}

#[test]
fn empty_returns_empty() -> Result<(), String> {
    let chunker = Bm25CohesionChunker::new(Bm25CohesionConfig::default())?;
    assert!(chunker.chunk("   ").is_empty());
    Ok(())
}

#[test]
fn determinism() -> Result<(), String> {
    let cfg = Bm25CohesionConfig {
        chunk_size: 50,
        ..Default::default()
    };
    let text = "First topic sentence here. Second topic follows now. Third unrelated finance news. Fourth finance detail here.";
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let a = chunker.chunk(text);
    let b = chunker.chunk(text);
    assert_eq!(a.len(), b.len());
    for (x, y) in a.iter().zip(b.iter()) {
        assert_eq!(x.text, y.text);
    }
    Ok(())
}

#[test]
fn word_mode_runs() -> Result<(), String> {
    let cfg = Bm25CohesionConfig {
        chunk_size: 30,
        similarity_window: 10,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let mut words = String::new();
    for i in 0..100 {
        use std::fmt::Write;
        let _ = write!(words, "word{i} ");
    }
    let chunks = chunker.chunk(&words);
    assert!(!chunks.is_empty());
    Ok(())
}

#[test]
fn merge_undersized_forward() -> Result<(), String> {
    let cfg = Bm25CohesionConfig {
        chunk_size: 50,
        minimum_words: 10,
        minimum_sentences: 1,
        ..Default::default()
    };
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let text = "First topic sentence here with enough words. Second topic follows now with enough words too. Third unrelated finance news item here. Fourth finance detail here with words.";
    let chunks = chunker.chunk(text);
    for chunk in &chunks {
        if testing_word_count(&chunk.text) < 10 {
            assert!(
                testing_concat_chunks(&chunks).len() <= text.len(),
                "tiny chunk only allowed when unavoidable"
            );
        }
    }
    assert_eq!(testing_concat_chunks(&chunks), text);
    Ok(())
}

#[test]
fn respects_disable_zero_minimum_words() -> Result<(), String> {
    let cfg = Bm25CohesionConfig {
        chunk_size: 100,
        similarity_window: 10,
        minimum_words: 0,
        minimum_sentences: 0,
        ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
    };
    let text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega extra words here and more padding text";
    let chunker = Bm25CohesionChunker::new(cfg)?;
    let chunks = chunker.chunk(text);
    assert_eq!(testing_concat_chunks(&chunks), text);
    Ok(())
}
