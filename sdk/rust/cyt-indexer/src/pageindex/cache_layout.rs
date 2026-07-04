use std::path::{Path, PathBuf};

pub const NODES_DIR: &str = "nodes";
pub const CHUNKS_DIR: &str = "chunks";
pub const PAGE_INDEX_FILE: &str = "page_index.json";
pub const CHUNK_INDEX_FILE: &str = "chunk_index.json";
pub const METADATA_FILE: &str = "metadata.json";

/// Catalog entry root: `{catalog_root}/entries/{content_hash}/`.
#[must_use]
pub fn skill_entry_dir(catalog_root: &Path, content_hash: &str) -> PathBuf {
    catalog_root.join("entries").join(content_hash)
}

#[must_use]
pub fn nodes_dir(entry_dir: &Path) -> PathBuf {
    entry_dir.join(NODES_DIR)
}

#[must_use]
pub fn page_index_path(entry_dir: &Path) -> PathBuf {
    nodes_dir(entry_dir).join(PAGE_INDEX_FILE)
}

#[must_use]
pub fn metadata_path(entry_dir: &Path) -> PathBuf {
    entry_dir.join(METADATA_FILE)
}

/// Content hash from `entries/{hash}/` directory name.
#[must_use]
pub fn entry_content_hash(entry_dir: &Path) -> Option<String> {
    entry_dir
        .file_name()
        .and_then(|n| n.to_str())
        .map(str::to_string)
}

#[must_use]
pub fn node_md_path(entry_dir: &Path, node_id: u32) -> PathBuf {
    nodes_dir(entry_dir).join(node_md_filename(node_id))
}

#[must_use]
pub fn chunk_variant_dir(entry_dir: &Path, pipeline: &str, params_hash: &str) -> PathBuf {
    entry_dir
        .join(CHUNKS_DIR)
        .join(normalize_pipeline(pipeline))
        .join(params_hash)
}

#[must_use]
pub fn chunk_index_path(variant_dir: &Path) -> PathBuf {
    variant_dir.join(CHUNK_INDEX_FILE)
}

#[must_use]
pub fn chunk_md_path(variant_dir: &Path, chunk_id: u32) -> PathBuf {
    variant_dir.join(chunk_md_filename(chunk_id))
}

#[must_use]
pub const fn page_index_rel() -> &'static str {
    "nodes/page_index.json"
}

#[must_use]
pub fn node_md_rel(node_id: u32) -> String {
    format!("{NODES_DIR}/{}", node_md_filename(node_id))
}

#[must_use]
pub fn node_md_filename(node_id: u32) -> String {
    format!("n{node_id}.md")
}

#[must_use]
pub fn chunk_index_rel(pipeline: &str, params_hash: &str) -> String {
    format!(
        "{CHUNKS_DIR}/{}/{params_hash}/{CHUNK_INDEX_FILE}",
        normalize_pipeline(pipeline)
    )
}

#[must_use]
pub fn chunk_md_rel(pipeline: &str, params_hash: &str, chunk_id: u32) -> String {
    format!(
        "{CHUNKS_DIR}/{}/{params_hash}/{}",
        normalize_pipeline(pipeline),
        chunk_md_filename(chunk_id)
    )
}

#[must_use]
pub fn chunk_md_filename(chunk_id: u32) -> String {
    format!("c{chunk_id}.md")
}

#[must_use]
pub fn normalize_pipeline(pipeline: &str) -> String {
    pipeline.trim().to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
