use std::fs;
use std::path::Path;

use super::cache_layout::{chunk_md_rel, chunk_variant_dir, node_md_rel};
use super::config::PageIndexConfig;
use super::document_json::write_chunk_index_structure;
use super::node_id::node_id_from_value;
use super::retrieve::strip_decomposed_frontmatter;
use super::tree::structure_to_list;
use super::types::SkillsIndex;
use crate::skills_io::load_skills_index_from_entry;

pub(crate) fn populate_structure_text_from_node_files(
    structure: &mut serde_json::Value,
    index: &SkillsIndex,
    _doc_id: &str,
) {
    populate_node_text(structure, index);
}

fn populate_node_text(structure: &mut serde_json::Value, index: &SkillsIndex) {
    match structure {
        serde_json::Value::Object(map) => {
            if map.contains_key("node_id") {
                let node_id = node_id_from_value(map.get("node_id"));
                let rel = node_md_rel(node_id);
                if let Some(raw) = index.files.get(&rel) {
                    let text = strip_decomposed_frontmatter(raw);
                    if !text.is_empty() {
                        map.insert("text".to_string(), serde_json::Value::String(text));
                    }
                }
            }
            if let Some(serde_json::Value::Array(children)) = map.get_mut("nodes") {
                for child in children {
                    populate_node_text(child, index);
                }
            }
        }
        serde_json::Value::Array(items) => {
            for item in items {
                populate_node_text(item, index);
            }
        }
        _ => {}
    }
}

fn chunk_file_exists(entry_dir: &Path, pipeline: &str, params_hash: &str, chunk_id: u32) -> bool {
    chunk_variant_dir(entry_dir, pipeline, params_hash)
        .join(format!("c{chunk_id}.md"))
        .is_file()
}

fn attach_missing_chunks_to_structure(
    structure: &mut serde_json::Value,
    config: &PageIndexConfig,
    index: &mut SkillsIndex,
    doc_id: &str,
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
) -> Result<bool, String> {
    let chunker =
        crate::bm25_cohesion::Bm25CohesionChunker::new(config.cohesion_config_for_chunking())?;
    let mut next_id = super::chunk_id::next_chunk_id(structure);
    let mut changed = false;

    let nodes = structure_to_list(structure);
    for node in nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let node_id = node_id_from_value(obj.get("node_id"));
        let line_num = obj
            .get("line_num")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0);
        let text = obj.get("text").and_then(|v| v.as_str()).unwrap_or("");
        if text.trim().is_empty() {
            continue;
        }

        let all_exist = obj
            .get("chunks")
            .and_then(serde_json::Value::as_array)
            .is_some_and(|arr| {
                !arr.is_empty()
                    && arr.iter().all(|chunk| {
                        chunk
                            .get("chunk_id")
                            .and_then(serde_json::Value::as_u64)
                            .and_then(|id| u32::try_from(id).ok())
                            .is_some_and(|id| {
                                chunk_file_exists(entry_dir, pipeline, params_hash, id)
                            })
                    })
            });
        if all_exist {
            continue;
        }

        let mut chunks = chunker.chunk(text);
        if chunks.is_empty() && !text.trim().is_empty() {
            chunks.push(crate::bm25_cohesion::CohesionChunk {
                text: text.to_string(),
                start_index: 0,
                end_index: text.len(),
                token_count: 1,
            });
        }
        let mut chunk_refs = Vec::new();
        for chunk in &chunks {
            let chunk_id = next_id;
            next_id += 1;
            chunk_refs.push(serde_json::json!({ "chunk_id": chunk_id }));
            let md = format!(
                "---\ndoc_id: {doc_id}\nnode_id: {node_id}\nchunk_id: {chunk_id}\nline_num: {line_num}\ntoken_count: {}\n---\n{}",
                chunk.token_count, chunk.text
            );
            let rel = chunk_md_rel(pipeline, params_hash, chunk_id);
            index.files.insert(rel.clone(), md.clone());
            let path = entry_dir.join(&rel);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            fs::write(&path, md).map_err(|e| e.to_string())?;
        }

        super::decompose::insert_chunks_on_node(structure, node_id, chunk_refs);
        changed = true;
    }

    index.chunk_pipeline = Some(pipeline.to_string());
    index.chunk_params_hash = Some(params_hash.to_string());

    Ok(changed)
}

/// Repair missing chunk files for a cached chunk variant directory.
///
/// # Errors
///
/// Returns an error when the entry directory is invalid or chunk files cannot be written.
pub fn repair_skill_variant_chunks(
    entry_dir: &Path,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
    config: &PageIndexConfig,
) -> Result<(), String> {
    let variant_dir = chunk_variant_dir(entry_dir, pipeline, params_hash);
    let mut index = load_skills_index_from_entry(entry_dir, doc_id, Some(variant_dir.as_path()))?;
    let doc = index
        .documents
        .get(doc_id)
        .cloned()
        .ok_or_else(|| format!("skill document not found: {doc_id}"))?;
    let mut structure = doc.structure.clone();

    populate_structure_text_from_node_files(&mut structure, &index, doc_id);
    let changed = attach_missing_chunks_to_structure(
        &mut structure,
        config,
        &mut index,
        doc_id,
        entry_dir,
        pipeline,
        params_hash,
    )?;

    if !changed {
        return Ok(());
    }

    write_chunk_index_structure(entry_dir, pipeline, params_hash, &structure)?;

    let mut updated = doc;
    updated.structure = structure;
    index.documents.insert(doc_id.to_string(), updated);
    Ok(())
}

/// Repair missing BM25 chunk files for a cached skill entry (bm25 variant).
///
/// # Errors
///
/// Returns an error when the entry directory is invalid or chunk files cannot be written.
pub fn repair_skill_chunks(
    entry_dir: &Path,
    doc_id: &str,
    config: &PageIndexConfig,
) -> Result<(), String> {
    repair_skill_variant_chunks(entry_dir, doc_id, "bm25", "default", config)
}

/// Backward-compatible repair entry point with explicit params hash.
///
/// # Errors
///
/// Returns an error when the entry directory is invalid or chunk files cannot be written.
pub fn repair_skill_chunks_variant(
    entry_dir: &Path,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
    config: &PageIndexConfig,
) -> Result<(), String> {
    repair_skill_variant_chunks(entry_dir, doc_id, pipeline, params_hash, config)
}
