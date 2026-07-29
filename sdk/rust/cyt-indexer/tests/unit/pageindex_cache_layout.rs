use std::path::{Path, PathBuf};

use cyt_indexer::pageindex::{
    chunk_md_path, chunk_md_rel, chunk_variant_dir, node_md_path, node_md_rel, page_index_path,
    skill_entry_dir,
};

#[test]
fn entry_layout_paths() {
    let root = Path::new("/catalog");
    let entry = skill_entry_dir(root, "abc123");
    assert_eq!(entry, PathBuf::from("/catalog/entries/abc123"));
    assert_eq!(
        page_index_path(&entry),
        PathBuf::from("/catalog/entries/abc123/nodes/page_index.json")
    );
    assert_eq!(
        node_md_path(&entry, 0),
        PathBuf::from("/catalog/entries/abc123/nodes/n0.md")
    );
    let variant = chunk_variant_dir(&entry, "BM25", "hash1");
    assert_eq!(
        variant,
        PathBuf::from("/catalog/entries/abc123/chunks/bm25/hash1")
    );
    assert_eq!(
        chunk_md_path(&variant, 3),
        PathBuf::from("/catalog/entries/abc123/chunks/bm25/hash1/c3.md")
    );
    assert_eq!(node_md_rel(1), "nodes/n1.md");
    assert_eq!(chunk_md_rel("llm", "h", 2), "chunks/llm/h/c2.md");
}
