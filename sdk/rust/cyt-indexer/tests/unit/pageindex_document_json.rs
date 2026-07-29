use std::fs;

use cyt_indexer::pageindex::document_json::{
    load_merged_document_from_entry, write_chunk_variant_index,
};
use cyt_indexer::pageindex::types::build_skill_document;
use cyt_indexer::pageindex::{
    ChunkVariantMetadata, EntryMetadata, MdIndexResult, PageIndexConfig, chunk_index_path,
    chunk_variant_dir, page_index_path, parse_document_on_disk, read_document_json,
    write_entry_metadata, write_page_index_files,
};
use serde_json::json;

#[test]
fn split_and_merge_document_json_roundtrip() -> Result<(), String> {
    let dir = std::env::temp_dir().join(format!("cyt-split-doc-{}", std::process::id()));
    let entry_dir = dir.join("entry");
    let doc_id = "skill";
    let _ = fs::remove_dir_all(&dir);

    let structure = json!([{
        "node_id": 1,
        "title": "Root",
        "chunks": [{"chunk_id": 0}]
    }]);
    let doc = build_skill_document(
        doc_id.to_string(),
        "~/skills/skill.md",
        &MdIndexResult {
            doc_name: "skill".to_string(),
            line_count: 4,
            structure: structure.clone(),
        },
        &PageIndexConfig::default(),
        Some("name: demo".to_string()),
        None,
    );
    let metadata = EntryMetadata {
        source_path: "/tmp/skills/skill.md".to_string(),
        pipeline: "bm25".to_string(),
        index_params: json!({"enable_bm25_chunking": true}),
    };
    write_page_index_files(&entry_dir, &doc)?;
    write_entry_metadata(&entry_dir, &metadata)?;
    write_chunk_variant_index(
        &entry_dir,
        "bm25",
        "hash1",
        &structure,
        &ChunkVariantMetadata {
            pipeline: "bm25".to_string(),
            index_params: metadata.index_params,
        },
    )?;

    let page = read_document_json(&page_index_path(&entry_dir))?;
    assert!(page.pointer("/structure/0/chunks").is_none());
    assert!(page.get("content_sha256").is_none());
    assert!(page.get("built_at").is_none());

    let variant = chunk_variant_dir(&entry_dir, "bm25", "hash1");
    let chunk_index = read_document_json(&chunk_index_path(&variant))?;
    assert!(chunk_index.pointer("/structure/0/chunks").is_some());
    assert_eq!(chunk_index["pipeline"], "bm25");

    let merged = load_merged_document_from_entry(&entry_dir, Some(&variant))?;
    let parsed = parse_document_on_disk(&merged).ok_or("parseable merged document")?;
    assert_eq!(parsed.doc_id, doc_id);
    assert_eq!(parsed.frontmatter.as_deref(), Some("name: demo"));
    assert!(parsed.structure.get(0).is_some());

    let _ = fs::remove_dir_all(&dir);
    Ok(())
}
