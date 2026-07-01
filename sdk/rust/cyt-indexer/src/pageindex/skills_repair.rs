use std::fs;
use std::path::Path;

use serde_json::Value;

use crate::bm25_cohesion::{Bm25CohesionChunker, CohesionChunk};

use super::chunk_id::next_chunk_id;
use super::config::PageIndexConfig;
use super::decompose::insert_chunks_on_node;
use super::node_id::node_id_from_value;
use super::retrieve::strip_decomposed_frontmatter;
use super::tree::structure_to_list;
use super::types::{SkillsIndex, chunk_md_rel, document_json_rel, node_md_rel};
use crate::skills_io::skills_index_from_decomposed_dir;

fn populate_structure_text_from_node_files(
    structure: &mut Value,
    index: &SkillsIndex,
    doc_id: &str,
) {
    populate_node_text(structure, index, doc_id);
}

fn populate_node_text(structure: &mut Value, index: &SkillsIndex, doc_id: &str) {
    match structure {
        Value::Object(map) => {
            if map.contains_key("node_id") {
                let node_id = node_id_from_value(map.get("node_id"));
                let rel = node_md_rel(doc_id, node_id);
                if let Some(raw) = index.files.get(&rel) {
                    let text = strip_decomposed_frontmatter(raw);
                    if !text.is_empty() {
                        map.insert("text".to_string(), Value::String(text));
                    }
                }
            }
            if let Some(Value::Array(children)) = map.get_mut("nodes") {
                for child in children {
                    populate_node_text(child, index, doc_id);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                populate_node_text(item, index, doc_id);
            }
        }
        _ => {}
    }
}

fn chunk_file_exists(entry_dir: &Path, doc_id: &str, chunk_id: u32) -> bool {
    entry_dir.join(chunk_md_rel(doc_id, chunk_id)).is_file()
}

fn chunks_for_text(chunker: &Bm25CohesionChunker, text: &str) -> Vec<CohesionChunk> {
    let mut chunks = chunker.chunk(text);
    if chunks.is_empty() && !text.trim().is_empty() {
        chunks.push(CohesionChunk {
            text: text.to_string(),
            start_index: 0,
            end_index: text.len(),
            token_count: 1,
        });
    }
    chunks
}

fn attach_missing_chunks_to_structure(
    structure: &mut Value,
    config: &PageIndexConfig,
    index: &mut SkillsIndex,
    doc_id: &str,
    entry_dir: &Path,
) -> Result<bool, String> {
    let chunker = Bm25CohesionChunker::new(config.cohesion_config_for_chunking())?;
    let mut next_id = next_chunk_id(structure);
    let mut changed = false;

    let nodes = structure_to_list(structure);
    for node in nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let node_id = node_id_from_value(obj.get("node_id"));
        let line_num = obj.get("line_num").and_then(Value::as_u64).unwrap_or(0);
        let text = obj.get("text").and_then(|v| v.as_str()).unwrap_or("");
        if text.trim().is_empty() {
            continue;
        }

        let all_exist = obj
            .get("chunks")
            .and_then(Value::as_array)
            .is_some_and(|arr| {
                !arr.is_empty()
                    && arr.iter().all(|chunk| {
                        chunk
                            .get("chunk_id")
                            .and_then(Value::as_u64)
                            .and_then(|id| u32::try_from(id).ok())
                            .is_some_and(|id| chunk_file_exists(entry_dir, doc_id, id))
                    })
            });
        if all_exist {
            continue;
        }

        let chunks = chunks_for_text(&chunker, text);
        let mut chunk_refs = Vec::new();
        for chunk in &chunks {
            let chunk_id = next_id;
            next_id += 1;
            chunk_refs.push(serde_json::json!({ "chunk_id": chunk_id }));
            let md = format!(
                "---\ndoc_id: {doc_id}\nnode_id: {node_id}\nchunk_id: {chunk_id}\nline_num: {line_num}\ntoken_count: {}\n---\n{}",
                chunk.token_count, chunk.text
            );
            let rel = chunk_md_rel(doc_id, chunk_id);
            index.files.insert(rel.clone(), md.clone());
            let path = entry_dir.join(&rel);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            fs::write(&path, md).map_err(|e| e.to_string())?;
        }

        insert_chunks_on_node(structure, node_id, chunk_refs);
        changed = true;
    }

    Ok(changed)
}

/// Repair missing BM25 chunk files for a cached decomposed skill entry.
///
/// # Errors
///
/// Returns an error when the entry directory is invalid or chunk files cannot be written.
pub fn repair_skill_chunks(
    entry_dir: &Path,
    doc_id: &str,
    config: &PageIndexConfig,
) -> Result<(), String> {
    let mut index = skills_index_from_decomposed_dir(entry_dir)?;
    let doc = index
        .documents
        .get(doc_id)
        .cloned()
        .ok_or_else(|| format!("skill document not found: {doc_id}"))?;
    let mut structure = doc.structure.clone();

    populate_structure_text_from_node_files(&mut structure, &index, doc_id);
    let changed =
        attach_missing_chunks_to_structure(&mut structure, config, &mut index, doc_id, entry_dir)?;

    if !changed {
        return Ok(());
    }

    let doc_path = entry_dir.join(document_json_rel(doc_id));
    let mut doc_val: Value = if doc_path.is_file() {
        let raw = fs::read_to_string(&doc_path).map_err(|e| e.to_string())?;
        serde_json::from_str(&raw).map_err(|e| e.to_string())?
    } else {
        doc.to_json()
    };
    if let Some(obj) = doc_val.as_object_mut() {
        obj.insert("structure".to_string(), structure.clone());
    }
    let serialized = serde_json::to_string_pretty(&doc_val).map_err(|e| e.to_string())?;
    fs::write(&doc_path, serialized).map_err(|e| e.to_string())?;

    let mut updated = doc;
    updated.structure = structure;
    index.documents.insert(doc_id.to_string(), updated);
    Ok(())
}
