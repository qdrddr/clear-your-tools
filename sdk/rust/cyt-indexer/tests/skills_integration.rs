use cyt_indexer::{
    build_skills_index, get_skill_document, get_skill_line_content_from_spec, get_skill_structure,
    load_skills_index_from_dir, skills_index_from_decomposed_dir,
    PageIndexConfig, SkillsBuilder, SkillsIndex,
};
use std::fs;
use std::path::{Path, PathBuf};

fn strip_frontmatter(content: &str) -> String {
    content
        .strip_prefix("---")
        .and_then(|rest| rest.find("\n---\n").map(|idx| rest[idx + 5..].to_string()))
        .unwrap_or_else(|| content.to_string())
}

fn word_mode_chunk_config() -> PageIndexConfig {
    PageIndexConfig::from_value(&serde_json::json!({
        "bm25_cohesion": {
            "window_mode": "word",
            "similarity_window": 10,
            "chunk_size": 100,
            "skip_window": 0
        }
    }))
}

fn find_split_section_chunk_ids(
    index: &SkillsIndex,
    doc_id: &str,
    section_marker: &str,
) -> Result<(u32, Vec<u32>), String> {
    use cyt_indexer::pageindex::node_id::node_id_from_value;
    use cyt_indexer::pageindex::tree::structure_to_list;

    let doc = index.documents.get(doc_id).ok_or("missing doc")?;
    for node in structure_to_list(&doc.structure) {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let chunk_count = obj
            .get("chunks")
            .and_then(|v| v.as_array())
            .map_or(0, std::vec::Vec::len);
        if chunk_count <= 1 {
            continue;
        }
        let node_id = node_id_from_value(obj.get("node_id"));
        let rel = format!("skills/decomposed/{doc_id}/{node_id}.md");
        let body = strip_frontmatter(index.files.get(&rel).ok_or("missing node md")?);
        if !body.contains(section_marker) {
            continue;
        }
        let chunk_ids = obj
            .get("chunks")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|chunk| chunk.get("chunk_id").and_then(serde_json::Value::as_u64))
                    .filter_map(|id| u32::try_from(id).ok())
                    .collect()
            })
            .unwrap_or_default();
        return Ok((node_id, chunk_ids));
    }
    Err("missing target section node".to_string())
}

fn concat_chunk_bodies(
    index: &SkillsIndex,
    doc_id: &str,
    chunk_ids: &[u32],
) -> Result<String, String> {
    use cyt_indexer::pageindex::types::chunk_md_rel;

    let mut concatenated = String::new();
    for chunk_id in chunk_ids {
        let rel = chunk_md_rel(doc_id, *chunk_id);
        concatenated.push_str(&strip_frontmatter(
            index.files.get(&rel).ok_or("missing chunk")?,
        ));
    }
    Ok(concatenated)
}

fn fixture_skills_dir(base: &Path) -> Result<PathBuf, String> {
    let dir = base.join("skills-src");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    fs::write(
        dir.join("create-hook.md"),
        "# Create Hook\n\nIntro\n\n## Usage\n\nRun the hook.\n\n## API\n\nDetails here.",
    )
    .map_err(|e| e.to_string())?;
    Ok(dir)
}

#[test]
fn in_memory_build_and_retrieve() -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!("cyt-skills-mem-{}", std::process::id()));
    let skills_dir = fixture_skills_dir(&tmp)?;
    let index = build_skills_index(&[skills_dir], &PageIndexConfig::default())?;
    assert_eq!(index.documents.len(), 1);
    let doc_id = "create-hook";
    let meta = get_skill_document(&index.documents, doc_id);
    assert_eq!(meta.get("doc_name").and_then(|v| v.as_str()), Some("create-hook"));
    let structure = get_skill_structure(&index.documents, doc_id);
    assert!(structure.is_array());
    let content = get_skill_line_content_from_spec(&index, doc_id, "1,5");
    let arr = content
        .as_array()
        .ok_or_else(|| "expected content array".to_string())?;
    assert!(!arr.is_empty());
    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn write_reconstruct_and_retrieve_via_cli_flow() -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!("cyt-skills-disk-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let skills_dir = fixture_skills_dir(&tmp)?;
    let catalog = tmp.join("catalog");

    let mut builder = SkillsBuilder::new(false, Some(catalog.clone()));
    builder.build_from_dirs(&[skills_dir], &PageIndexConfig::default())?;
    builder.write_catalog()?;

    assert!(catalog.join("skills_index.json").is_file());
    assert!(catalog.join("skills/decomposed/create-hook/document.json").is_file());

    fs::remove_file(catalog.join("skills_index.json")).map_err(|e| e.to_string())?;
    let rebuilt = skills_index_from_decomposed_dir(&catalog)?;
    assert_eq!(rebuilt.documents.len(), 1);

    let loaded = load_skills_index_from_dir(&catalog)?;
    let doc_id = "create-hook";
    let content = get_skill_line_content_from_spec(&loaded, doc_id, "5-10");
    let arr = content
        .as_array()
        .ok_or_else(|| "expected content array".to_string())?;
    assert!(!arr.is_empty());
    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn decomposed_markdown_preserves_original_header() -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!("cyt-skills-header-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let skills_dir = tmp.join("skills-src");
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    fs::write(
        skills_dir.join("skill.md"),
        "## When to Use\n\nBody text\n\n### Child\n\nMore",
    )
    .map_err(|e| e.to_string())?;

    let index = build_skills_index(&[skills_dir], &PageIndexConfig::default())?;
    let content = index
        .files
        .get("skills/decomposed/skill/2.md")
        .ok_or_else(|| "missing decomposed node file".to_string())?;

    assert!(
        !content.contains("title:"),
        "frontmatter should not repeat the heading title"
    );
    assert!(
        !content.contains("# When to Use\n\n## When to Use"),
        "decomposed body should not duplicate the heading"
    );
    assert!(
        content.contains("## When to Use\n\nBody text"),
        "decomposed body should preserve the original heading level"
    );

    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn build_without_bm25_chunking_returns_node_level_only() -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!("cyt-skills-no-chunk-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let skills_dir = fixture_skills_dir(&tmp)?;

    let index = build_skills_index(&[skills_dir], &PageIndexConfig::without_bm25_chunking())?;
    assert_eq!(index.documents.len(), 1);
    assert!(
        !index.files.keys().any(|k| k.contains("/chunks/")),
        "expected no chunk files when BM25 chunking is disabled"
    );
    assert!(
        index.files.keys().any(|k| k.ends_with("/document.json")),
        "expected document.json"
    );
    assert!(
        index.files.keys().any(|k| {
            Path::new(k)
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("md"))
                && !k.contains("/chunks/")
        }),
        "expected node-level markdown files"
    );

    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn word_mode_chunks_preserve_formatting_and_recompile() -> Result<(), String> {
    use cyt_indexer::pageindex::{reconstruct_skill_markdown, ReconstructOptions};

    const SECTION: &str = "### Step 2: Select the Best Match\n\nFrom the resolution results, choose based on:\n\n- Exact or closest name match to what the user asked for\n- Higher benchmark scores indicate better documentation quality\n- If the user mentioned a version (e.g., \"React 19\"), prefer version-specific IDs";

    let tmp = std::env::temp_dir().join(format!("cyt-chunk-fmt-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let skills_dir = tmp.join("skills-src");
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    fs::write(
        skills_dir.join("skill.md"),
        format!("# Doc\n\n## How To\n\n{SECTION}\n"),
    )
    .map_err(|e| e.to_string())?;

    let index = build_skills_index(&[skills_dir], &word_mode_chunk_config())?;
    let doc_id = "skill";

    let chunk_paths: Vec<_> = index
        .files
        .keys()
        .filter(|k| k.contains("/chunks/"))
        .cloned()
        .collect();
    assert!(chunk_paths.len() > 1, "expected section to split into multiple chunks");
    for path in &chunk_paths {
        let body = strip_frontmatter(index.files.get(path).ok_or("missing chunk file")?);
        assert!(body.contains(' '), "chunk should preserve spaces: {path}");
    }

    let (target_node_id, chunk_ids) =
        find_split_section_chunk_ids(&index, doc_id, "### Step 2: Select the Best Match")?;
    assert!(chunk_ids.len() > 1, "expected target section to split into multiple chunks");

    let parent_rel = format!("skills/decomposed/{doc_id}/{target_node_id}.md");
    let parent_body =
        strip_frontmatter(index.files.get(&parent_rel).ok_or("missing parent node file")?);
    assert_eq!(concat_chunk_bodies(&index, doc_id, &chunk_ids)?, parent_body);

    let chunk_spec = chunk_ids
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let reconstructed = reconstruct_skill_markdown(
        &index,
        doc_id,
        &[],
        &[],
        &[chunk_spec.as_str()],
        &ReconstructOptions::default(),
    )?;
    assert!(reconstructed.matched_chunk_ids.len() >= 2);
    assert!(reconstructed.markdown.contains("### Step 2: Select the Best Match"));
    assert!(reconstructed.markdown.contains("- Exact or closest name match"));

    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}
