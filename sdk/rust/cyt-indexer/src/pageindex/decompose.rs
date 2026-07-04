use serde_json::{Value, json};

use crate::bm25_cohesion::{Bm25CohesionChunker, CohesionChunk};

use super::cache_layout::{chunk_index_rel, chunk_md_rel, page_index_rel};
use super::chunk_id::next_chunk_id;
use super::config::PageIndexConfig;
use super::document_json::{build_page_index_json_value, serialize_document_json};
use super::node_id::node_id_from_value;
use super::tree::structure_to_list;
use super::types::{SkillDocument, SkillsIndex, node_md_rel};

/// Attach BM25 cohesion chunks to structure nodes and write chunk markdown files.
///
/// # Errors
///
/// Returns an error when the chunker configuration is invalid.
pub fn attach_chunks_to_structure(
    structure: &mut Value,
    config: &PageIndexConfig,
    index: &mut SkillsIndex,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
) -> Result<(), String> {
    let chunker = Bm25CohesionChunker::new(config.cohesion_config_for_chunking())?;
    let mut next_id = next_chunk_id(structure);

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

        let mut chunks = chunker.chunk(text);
        if chunks.is_empty() {
            chunks.push(CohesionChunk {
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
            chunk_refs.push(json!({ "chunk_id": chunk_id }));
            let md = format!(
                "---\ndoc_id: {doc_id}\nnode_id: {node_id}\nchunk_id: {chunk_id}\nline_num: {line_num}\ntoken_count: {}\n---\n{}",
                chunk.token_count, chunk.text
            );
            index
                .files
                .insert(chunk_md_rel(pipeline, params_hash, chunk_id), md);
        }

        insert_chunks_on_node(structure, node_id, chunk_refs);
    }

    index.chunk_pipeline = Some(pipeline.to_string());
    index.chunk_params_hash = Some(params_hash.to_string());

    Ok(())
}

pub(crate) fn insert_chunks_on_node(
    structure: &mut Value,
    target_node_id: u32,
    chunks: Vec<Value>,
) -> bool {
    match structure {
        Value::Object(map) => {
            let id = node_id_from_value(map.get("node_id"));
            if id == target_node_id {
                map.insert("chunks".to_string(), Value::Array(chunks));
                return true;
            }
            if let Some(Value::Array(children)) = map.get_mut("nodes") {
                for child in children {
                    if insert_chunks_on_node(child, target_node_id, chunks.clone()) {
                        return true;
                    }
                }
            }
            false
        }
        Value::Array(items) => {
            for item in items {
                if insert_chunks_on_node(item, target_node_id, chunks.clone()) {
                    return true;
                }
            }
            false
        }
        _ => false,
    }
}

/// Write node-only page index and node markdown files into the index file map.
pub fn decompose_page_index(index: &mut SkillsIndex, doc: &SkillDocument, flat_structure: &Value) {
    let page_json = serialize_document_json(&build_page_index_json_value(doc)).unwrap_or_default();
    index.files.insert(page_index_rel().to_string(), page_json);

    write_node_markdown_files(index, doc, flat_structure);
}

/// Write chunk variant index and chunk markdown files into the index file map.
pub fn decompose_chunk_variant(
    index: &mut SkillsIndex,
    structure: &Value,
    pipeline: &str,
    params_hash: &str,
    metadata: &super::document_json::ChunkVariantMetadata,
) {
    let chunk_json = serialize_document_json(&super::document_json::build_chunk_index_json_value(
        structure, metadata,
    ))
    .unwrap_or_default();
    index
        .files
        .insert(chunk_index_rel(pipeline, params_hash), chunk_json);
}

pub fn decompose_document(
    index: &mut SkillsIndex,
    doc: &SkillDocument,
    flat_structure: &Value,
    config: &PageIndexConfig,
) {
    decompose_page_index(index, doc, flat_structure);
    let chunk_metadata = super::document_json::ChunkVariantMetadata {
        pipeline: "bm25".to_string(),
        index_params: config.to_index_params_value(),
    };
    decompose_chunk_variant(index, &doc.structure, "bm25", "legacy", &chunk_metadata);
}

fn write_node_markdown_files(index: &mut SkillsIndex, doc: &SkillDocument, flat_structure: &Value) {
    let nodes = structure_to_list(flat_structure);
    for node in nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let node_id = node_id_from_value(obj.get("node_id"));
        let title = obj.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let line_num = obj.get("line_num").and_then(Value::as_u64).unwrap_or(0);
        let text = obj.get("text").and_then(|v| v.as_str()).unwrap_or("");

        let body = if super::tree::is_frontmatter_node(obj) || super::tree::is_preamble_node(obj) {
            text.to_string()
        } else if text.is_empty() {
            format!("# {title}\n")
        } else {
            text.to_string()
        };

        let md_content = format!(
            "---\ndoc_id: {}\nnode_id: {node_id}\nline_num: {line_num}\n---\n{body}",
            doc.id
        );

        index.files.insert(node_md_rel(node_id), md_content);
    }
}
