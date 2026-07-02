use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use super::cache_layout::{
    PAGE_INDEX_FILE, chunk_index_path, chunk_md_path, chunk_variant_dir, node_md_path, nodes_dir,
    page_index_path,
};
use super::types::SkillDocument;

/// Cache metadata stored alongside page-index fields in `page_index.json`.
#[derive(Debug, Clone)]
pub struct SkillDocumentExtras {
    pub content_sha256: String,
    pub pipeline: String,
    pub index_params: Value,
    pub built_at: String,
    pub source_path: String,
}

/// Parsed merged on-disk skill index fields used by BM25 and catalog loaders.
#[derive(Debug, Clone)]
pub struct SkillDocumentOnDisk {
    pub doc_id: String,
    pub path: String,
    pub structure: Value,
    pub frontmatter: Option<String>,
}

/// Store paths under `$HOME` as `~/...` in `page_index.json`.
///
/// # Errors
///
/// Returns an error when `HOME` is unset or the path cannot be expanded.
pub fn shorten_home_path(path: &str) -> Result<String, String> {
    let expanded = expand_path(Path::new(path))?;
    let home = home_dir()?;
    let path_str = expanded.to_string_lossy().replace('\\', "/");
    if path_str == home {
        return Ok("~".to_string());
    }
    let home_prefix = format!("{home}/");
    if let Some(rest) = path_str.strip_prefix(&home_prefix) {
        return Ok(format!("~/{rest}"));
    }
    Ok(path_str)
}

fn home_dir() -> Result<String, String> {
    std::env::var("HOME").map_err(|_| "HOME not set".to_string())
}

fn expand_path(path: &Path) -> Result<PathBuf, String> {
    let s = path.to_string_lossy();
    if s == "~" {
        return Ok(PathBuf::from(home_dir()?));
    }
    if let Some(stripped) = s.strip_prefix("~/") {
        return Ok(PathBuf::from(home_dir()?).join(stripped));
    }
    Ok(path.to_path_buf())
}

/// Remove BM25 chunk references from a page tree, leaving nodes only.
#[must_use]
pub fn strip_chunks_from_structure(structure: &Value) -> Value {
    match structure {
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, value) in map {
                if key == "chunks" {
                    continue;
                }
                if key == "nodes" {
                    if let Value::Array(children) = value {
                        out.insert(
                            key.clone(),
                            Value::Array(
                                children.iter().map(strip_chunks_from_structure).collect(),
                            ),
                        );
                    }
                    continue;
                }
                out.insert(key.clone(), value.clone());
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(strip_chunks_from_structure).collect())
        }
        other => other.clone(),
    }
}

/// Build `page_index.json`: document metadata plus node-only structure.
#[must_use]
pub fn build_page_index_json_value(
    doc: &SkillDocument,
    extras: Option<&SkillDocumentExtras>,
) -> Value {
    let mut value = doc.to_json();
    if let Some(obj) = value.as_object_mut()
        && let Some(structure) = obj.get("structure")
    {
        obj.insert(
            "structure".to_string(),
            strip_chunks_from_structure(structure),
        );
    }
    if let Some(extras) = extras {
        merge_document_extras(&mut value, extras);
    }
    value
}

/// Build `chunk_index.json`: full structure with nodes and chunk refs.
#[must_use]
pub fn build_chunk_index_json_value(structure: &Value) -> Value {
    json!({ "structure": structure })
}

pub fn merge_document_extras(value: &mut Value, extras: &SkillDocumentExtras) {
    let Some(obj) = value.as_object_mut() else {
        return;
    };
    obj.insert(
        "content_sha256".to_string(),
        Value::String(extras.content_sha256.clone()),
    );
    obj.insert(
        "pipeline".to_string(),
        Value::String(extras.pipeline.clone()),
    );
    obj.insert("index_params".to_string(), extras.index_params.clone());
    obj.insert(
        "built_at".to_string(),
        Value::String(extras.built_at.clone()),
    );
    if let Ok(path) = shorten_home_path(&extras.source_path) {
        obj.insert("path".to_string(), Value::String(path));
    }
}

/// Serialize index JSON with stable pretty formatting and trailing newline.
///
/// # Errors
///
/// Returns an error when the value cannot be serialized to JSON.
pub fn serialize_document_json(value: &Value) -> Result<String, String> {
    let mut serialized = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    serialized.push('\n');
    Ok(serialized)
}

/// Read and parse a JSON index file from disk.
///
/// # Errors
///
/// Returns an error when the file cannot be read or parsed as JSON.
pub fn read_document_json(path: &Path) -> Result<Value, String> {
    let raw = fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}

/// Write an index JSON file using the canonical on-disk format.
///
/// # Errors
///
/// Returns an error when parent directories or the file cannot be written.
pub fn write_document_json(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(path, serialize_document_json(value)?).map_err(|e| e.to_string())
}

/// Merge node metadata from `nodes/page_index.json` with chunk structure from a variant dir.
///
/// # Errors
///
/// Returns an error when `page_index.json` is missing or invalid.
pub fn load_merged_document_from_entry(
    entry_dir: &Path,
    chunk_variant: Option<&Path>,
) -> Result<Value, String> {
    let page_path = page_index_path(entry_dir);
    let mut page = read_document_json(&page_path).map_err(|e| {
        format!(
            "{PAGE_INDEX_FILE} not readable at {}: {e}",
            nodes_dir(entry_dir).display()
        )
    })?;
    if let Some(variant_dir) = chunk_variant {
        let chunk_path = chunk_index_path(variant_dir);
        if chunk_path.is_file() {
            let chunk_index = read_document_json(&chunk_path)?;
            if let Some(structure) = chunk_index.get("structure") {
                page.as_object_mut()
                    .ok_or_else(|| "page_index.json root is not an object".to_string())?
                    .insert("structure".to_string(), structure.clone());
            }
        }
    }
    Ok(page)
}

/// Load merged skill document JSON for one cached entry.
///
/// # Errors
///
/// Returns an error when page or chunk index files cannot be read or merged.
pub fn load_merged_document_json(
    entry_dir: &Path,
    _doc_id: &str,
    chunk_variant: Option<&Path>,
) -> Result<Value, String> {
    load_merged_document_from_entry(entry_dir, chunk_variant)
}

/// Write page-index files under `entry_dir/nodes/`.
///
/// # Errors
///
/// Returns an error when the page index file cannot be written.
pub fn write_page_index_files(
    entry_dir: &Path,
    doc: &SkillDocument,
    extras: Option<&SkillDocumentExtras>,
) -> Result<(), String> {
    write_document_json(
        &page_index_path(entry_dir),
        &build_page_index_json_value(doc, extras),
    )
}

/// Write chunk variant index under `chunks/{pipeline}/{params_hash}/`.
///
/// # Errors
///
/// Returns an error when the chunk index file cannot be written.
pub fn write_chunk_variant_index(
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
    structure: &Value,
) -> Result<(), String> {
    let variant_dir = chunk_variant_dir(entry_dir, pipeline, params_hash);
    write_document_json(
        &chunk_index_path(&variant_dir),
        &build_chunk_index_json_value(structure),
    )
}

/// Parse BM25/catalog-facing fields from a merged skill document value.
#[must_use]
pub fn parse_document_on_disk(value: &Value) -> Option<SkillDocumentOnDisk> {
    let obj = value.as_object()?;
    let doc_id = obj.get("id")?.as_str()?.to_string();
    Some(SkillDocumentOnDisk {
        doc_id,
        path: obj
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        structure: obj.get("structure")?.clone(),
        frontmatter: obj
            .get("frontmatter")
            .and_then(Value::as_str)
            .map(str::to_string),
    })
}

/// Merge cache metadata into an existing `nodes/page_index.json`.
///
/// # Errors
///
/// Returns an error when `page_index.json` cannot be read or written.
pub fn finalize_document_json(
    entry_dir: &Path,
    _doc_id: &str,
    extras: &SkillDocumentExtras,
) -> Result<Value, String> {
    let page_path = page_index_path(entry_dir);
    let mut value =
        read_document_json(&page_path).map_err(|e| format!("page_index.json not readable: {e}"))?;
    merge_document_extras(&mut value, extras);
    write_document_json(&page_path, &value)?;
    load_merged_document_from_entry(entry_dir, None)
}

/// Update only the canonical source path in `nodes/page_index.json`.
///
/// # Errors
///
/// Returns an error when `page_index.json` cannot be read or written.
pub fn update_document_source_path(
    entry_dir: &Path,
    _doc_id: &str,
    source_path: &str,
) -> Result<Value, String> {
    let page_path = page_index_path(entry_dir);
    let mut value =
        read_document_json(&page_path).map_err(|e| format!("page_index.json not readable: {e}"))?;
    let canonical_path = shorten_home_path(source_path)?;
    value
        .as_object_mut()
        .ok_or_else(|| "page_index.json is not an object".to_string())?
        .insert("path".to_string(), Value::String(canonical_path));
    write_document_json(&page_path, &value)?;
    load_merged_document_from_entry(entry_dir, None)
}

/// Persist an updated chunk-aware structure after repair.
///
/// # Errors
///
/// Returns an error when the chunk index file cannot be written.
pub fn write_chunk_index_structure(
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
    structure: &Value,
) -> Result<(), String> {
    write_chunk_variant_index(entry_dir, pipeline, params_hash, structure)
}

/// Check that all node markdown files referenced in page structure exist.
#[must_use]
pub fn page_index_files_complete(entry_dir: &Path, structure: &Value) -> bool {
    for node_id in iter_node_ids(structure) {
        if !node_md_path(entry_dir, node_id).is_file() {
            return false;
        }
    }
    true
}

/// Check that all chunk markdown files referenced in chunk structure exist.
#[must_use]
pub fn chunk_variant_files_complete(
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
    structure: &Value,
) -> bool {
    let variant_dir = chunk_variant_dir(entry_dir, pipeline, params_hash);
    if !chunk_index_path(&variant_dir).is_file() {
        return false;
    }
    for chunk_id in iter_chunk_ids(structure) {
        if !chunk_md_path(&variant_dir, chunk_id).is_file() {
            return false;
        }
    }
    true
}

fn iter_node_ids(structure: &Value) -> Vec<u32> {
    let mut ids = Vec::new();
    walk_node_ids(structure, &mut ids);
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn walk_node_ids(node: &Value, out: &mut Vec<u32>) {
    match node {
        Value::Object(map) => {
            if let Some(id) = map.get("node_id").and_then(Value::as_u64)
                && let Ok(node_id) = u32::try_from(id)
            {
                out.push(node_id);
            }
            if let Some(Value::Array(children)) = map.get("nodes") {
                for child in children {
                    walk_node_ids(child, out);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                walk_node_ids(item, out);
            }
        }
        _ => {}
    }
}

fn iter_chunk_ids(structure: &Value) -> Vec<u32> {
    let mut ids = Vec::new();
    walk_chunk_ids(structure, &mut ids);
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn walk_chunk_ids(node: &Value, out: &mut Vec<u32>) {
    match node {
        Value::Object(map) => {
            if let Some(chunks) = map.get("chunks").and_then(Value::as_array) {
                for chunk in chunks {
                    if let Some(id) = chunk.get("chunk_id").and_then(Value::as_u64)
                        && let Ok(chunk_id) = u32::try_from(id)
                    {
                        out.push(chunk_id);
                    }
                }
            }
            if let Some(Value::Array(children)) = map.get("nodes") {
                for child in children {
                    walk_chunk_ids(child, out);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                walk_chunk_ids(item, out);
            }
        }
        _ => {}
    }
}

#[must_use]
pub fn empty_index_params() -> Value {
    Value::Object(Map::new())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pageindex::types::build_skill_document;
    use crate::pageindex::{MdIndexResult, PageIndexConfig};

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
        let extras = SkillDocumentExtras {
            content_sha256: "abc".to_string(),
            pipeline: "bm25".to_string(),
            index_params: json!({"enable_bm25_chunking": true}),
            built_at: "2026-01-01T00:00:00+00:00".to_string(),
            source_path: "/tmp/skills/skill.md".to_string(),
        };
        write_page_index_files(&entry_dir, &doc, Some(&extras))?;
        write_chunk_variant_index(&entry_dir, "bm25", "hash1", &structure)?;

        let page = read_document_json(&page_index_path(&entry_dir))?;
        assert!(page.pointer("/structure/0/chunks").is_none());

        let variant = chunk_variant_dir(&entry_dir, "bm25", "hash1");
        let chunk_index = read_document_json(&chunk_index_path(&variant))?;
        assert!(chunk_index.pointer("/structure/0/chunks").is_some());

        let merged = load_merged_document_from_entry(&entry_dir, Some(&variant))?;
        let parsed = parse_document_on_disk(&merged).ok_or("parseable merged document")?;
        assert_eq!(parsed.doc_id, doc_id);
        assert_eq!(parsed.frontmatter.as_deref(), Some("name: demo"));
        assert!(parsed.structure.get(0).is_some());

        let _ = fs::remove_dir_all(&dir);
        Ok(())
    }
}
