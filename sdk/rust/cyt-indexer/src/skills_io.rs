use std::fs;
use std::path::Path;

use crate::pageindex::cache_layout::{CHUNKS_DIR, NODES_DIR, nodes_dir, page_index_rel};
use crate::pageindex::document_json::{load_merged_document_from_entry, write_bytes_atomic};
use crate::pageindex::{SkillDocument, SkillsIndex};

/// Write node and page-index files from an in-memory index to `entry_dir`.
///
/// # Errors
///
/// Returns an error when directories or files cannot be created or written.
pub fn write_page_index_files(index: &SkillsIndex, entry_dir: &Path) -> Result<(), String> {
    fs::create_dir_all(entry_dir).map_err(|e| e.to_string())?;
    write_selected_files(index, entry_dir, |rel| {
        rel == page_index_rel() || rel.starts_with(&format!("{NODES_DIR}/"))
    })
}

/// Write chunk variant files from an in-memory index to `entry_dir`.
///
/// # Errors
///
/// Returns an error when directories or files cannot be created or written.
pub fn write_chunk_variant_files(
    index: &SkillsIndex,
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
) -> Result<(), String> {
    let prefix = format!(
        "{CHUNKS_DIR}/{}/{}",
        pipeline.trim().to_ascii_lowercase(),
        params_hash
    );
    write_selected_files(index, entry_dir, |rel| rel.starts_with(&prefix))
}

/// Write all known index files (page + any chunk variants present in the file map).
///
/// # Errors
///
/// Returns an error when directories or files cannot be created or written.
pub fn write_skills_index(index: &SkillsIndex, output_dir: &Path) -> Result<(), String> {
    fs::create_dir_all(output_dir).map_err(|e| e.to_string())?;
    write_selected_files(index, output_dir, |_| true)
}

fn write_selected_files(
    index: &SkillsIndex,
    output_dir: &Path,
    include: impl Fn(&str) -> bool,
) -> Result<(), String> {
    for (rel, content) in &index.files {
        if !include(rel) {
            continue;
        }
        let path = output_dir.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        write_bytes_atomic(&path, content.as_bytes())?;
    }
    Ok(())
}

/// Write node markdown files referenced in the index file map.
///
/// # Errors
///
/// Returns an error when files cannot be written.
pub fn write_node_files_from_index(index: &SkillsIndex, entry_dir: &Path) -> Result<(), String> {
    write_selected_files(index, entry_dir, |rel| {
        rel.starts_with(&format!("{NODES_DIR}/"))
    })
}

/// Load page-index-only skills index from an entry directory.
///
/// # Errors
///
/// Returns an error when the entry directory is invalid or files cannot be read.
pub fn load_page_index_from_entry(entry_dir: &Path, doc_id: &str) -> Result<SkillsIndex, String> {
    load_skills_index_from_entry(entry_dir, doc_id, None)
}

/// Load a skills index from an entry directory, optionally overlaying a chunk variant.
///
/// # Errors
///
/// Returns an error when the catalog directory is invalid or files cannot be read.
pub fn load_skills_index_from_entry(
    entry_dir: &Path,
    doc_id: &str,
    chunk_variant: Option<&Path>,
) -> Result<SkillsIndex, String> {
    if let Some(cached) = crate::cache::get_skills_index(entry_dir, doc_id, chunk_variant) {
        return Ok(cached);
    }
    let index = load_skills_index_from_entry_impl(entry_dir, doc_id, chunk_variant)?;
    crate::cache::store_skills_index(entry_dir, doc_id, chunk_variant, index.clone());
    Ok(index)
}

fn load_skills_index_from_entry_impl(
    entry_dir: &Path,
    doc_id: &str,
    chunk_variant: Option<&Path>,
) -> Result<SkillsIndex, String> {
    let mut index = SkillsIndex::default();
    load_entry_files_into_index(entry_dir, chunk_variant, &mut index)?;

    let merged = load_merged_document_from_entry(entry_dir, chunk_variant)?;
    if let Some(doc) = SkillDocument::from_json(&merged) {
        index.documents.insert(doc_id.to_string(), doc);
    }

    if let Some(variant_dir) = chunk_variant {
        let pipeline = variant_dir
            .parent()
            .and_then(|p| p.file_name())
            .map(|n| n.to_string_lossy().into_owned());
        let params_hash = variant_dir
            .file_name()
            .map(|n| n.to_string_lossy().into_owned());
        index.chunk_pipeline = pipeline;
        index.chunk_params_hash = params_hash;
    }

    if index.documents.is_empty() {
        return Err(format!(
            "no skill document found in entry directory: {}",
            entry_dir.display()
        ));
    }
    Ok(index)
}

/// Reload a skills index from disk and refresh the hot cache entry.
pub fn refresh_skills_index_cache(entry_dir: &Path, doc_id: &str, chunk_variant: Option<&Path>) {
    if let Ok(index) = load_skills_index_from_entry_impl(entry_dir, doc_id, chunk_variant) {
        crate::cache::store_skills_index(entry_dir, doc_id, chunk_variant, index);
    }
}

/// Load a skills index from an entry directory (page index + optional default chunk overlay).
///
/// # Errors
///
/// Returns an error when the catalog directory is invalid or files cannot be read.
pub fn load_skills_index_from_dir(catalog_dir: &Path) -> Result<SkillsIndex, String> {
    let doc_id = infer_doc_id_from_entry(catalog_dir)?;
    load_skills_index_from_entry(catalog_dir, &doc_id, None)
}

/// Reconstruct a skills index from an entry directory.
///
/// # Errors
///
/// Returns an error when the entry directory is missing or contains no documents.
pub fn skills_index_from_decomposed_dir(dir: &Path) -> Result<SkillsIndex, String> {
    load_skills_index_from_dir(dir)
}

impl SkillsIndex {
    /// Reconstruct a skills index from files under `catalog_dir`.
    ///
    /// # Errors
    ///
    /// Returns an error when the entry directory is missing or contains no documents.
    pub fn from_decomposed_dir(catalog_dir: &Path) -> Result<Self, String> {
        load_skills_index_from_dir(catalog_dir)
    }
}

/// Load entry files from disk into an existing index.
///
/// # Errors
///
/// Returns an error when files cannot be read.
pub fn load_decomposed_files_for_index(
    catalog_dir: &Path,
    index: &mut SkillsIndex,
) -> Result<(), String> {
    load_entry_files_into_index(catalog_dir, None, index)
}

fn load_entry_files_into_index(
    entry_dir: &Path,
    chunk_variant: Option<&Path>,
    index: &mut SkillsIndex,
) -> Result<(), String> {
    let nodes = nodes_dir(entry_dir);
    if nodes.is_dir() {
        load_dir_files(entry_dir, &nodes, index)?;
    }

    if let Some(variant_dir) = chunk_variant {
        if variant_dir.is_dir() {
            load_dir_files(entry_dir, variant_dir, index)?;
        }
    } else if let Ok(entries) = fs::read_dir(entry_dir.join(CHUNKS_DIR)) {
        for entry in entries.flatten() {
            let pipeline_dir = entry.path();
            if !pipeline_dir.is_dir() {
                continue;
            }
            if let Ok(hash_entries) = fs::read_dir(&pipeline_dir) {
                for hash_entry in hash_entries.flatten() {
                    let variant = hash_entry.path();
                    if variant.is_dir() {
                        load_dir_files(entry_dir, &variant, index)?;
                    }
                }
            }
        }
    }
    Ok(())
}

fn load_dir_files(entry_dir: &Path, dir: &Path, index: &mut SkillsIndex) -> Result<(), String> {
    for file_entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let file_entry = file_entry.map_err(|e| e.to_string())?;
        let path = file_entry.path();
        if !path.is_file() {
            continue;
        }
        let rel = path
            .strip_prefix(entry_dir)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        index.files.insert(rel, content);
    }
    Ok(())
}

fn infer_doc_id_from_entry(entry_dir: &Path) -> Result<String, String> {
    let page_path = entry_dir.join(page_index_rel());
    if page_path.is_file() {
        let raw = fs::read_to_string(&page_path).map_err(|e| e.to_string())?;
        let value: serde_json::Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
        if let Some(id) = value.get("id").and_then(|v| v.as_str()) {
            return Ok(id.to_string());
        }
    }
    Err(format!(
        "could not infer doc_id from entry directory: {}",
        entry_dir.display()
    ))
}

pub fn merge_skills_index_files(index: &mut SkillsIndex, other: &SkillsIndex) {
    index.documents.extend(other.documents.clone());
    index.files.extend(other.files.clone());
    if index.chunk_pipeline.is_none() {
        index.chunk_pipeline.clone_from(&other.chunk_pipeline);
    }
    if index.chunk_params_hash.is_none() {
        index.chunk_params_hash.clone_from(&other.chunk_params_hash);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pageindex::{PageIndexConfig, build_skills_index};

    #[test]
    fn write_and_reconstruct_from_split_index_files() -> Result<(), String> {
        let dir = std::env::temp_dir().join(format!("cyt-skills-{}", std::process::id()));
        let skills_dir = dir.join("skills-src");
        let entry_dir = dir.join("entry");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("skill.md"),
            "# Root\n\nBody\n\n## Child\n\nMore",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_dir], &PageIndexConfig::default())?;
        write_skills_index(&index, &entry_dir)?;

        assert!(entry_dir.join("nodes/page_index.json").is_file());
        assert!(
            entry_dir
                .join("chunks/bm25/default/chunk_index.json")
                .is_file()
        );

        let rebuilt = load_skills_index_from_entry(&entry_dir, "skill", None)?;
        assert_eq!(rebuilt.documents.len(), index.documents.len());
        assert!(!rebuilt.files.is_empty());
        let _ = fs::remove_dir_all(&dir);
        Ok(())
    }
}
