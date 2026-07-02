use std::fs;
use std::path::{Path, PathBuf};

use super::cache_layout::{chunk_variant_dir, page_index_path};
use super::config::PageIndexConfig;
use super::decompose::{attach_chunks_to_structure, decompose_chunk_variant, decompose_page_index};
use super::document_json::{
    SkillDocumentExtras, load_merged_document_from_entry, page_index_files_complete,
    read_document_json, write_page_index_files,
};
use super::index::md_to_tree;
use super::parse::{extract_node_text_content, extract_nodes_from_markdown, extract_skill_prefix};
use super::skills_repair::populate_structure_text_from_node_files;
use super::tree::{build_tree_from_nodes, finalize_skill_structure};
use super::types::{
    MdIndexResult, SkillDocument, SkillsIndex, build_skill_document, doc_id_from_rel_path,
};

/// Build an in-memory skills index from one or more skill directories.
///
/// # Errors
///
/// Returns an error when a directory is missing or a markdown file cannot be read.
pub fn build_skills_index(
    skill_dirs: &[PathBuf],
    config: &PageIndexConfig,
) -> Result<SkillsIndex, String> {
    build_skills_index_with_options(skill_dirs, config, true, None, None)
}

/// Build a page-index-only skills index (nodes without chunk variants).
///
/// # Errors
///
/// Returns an error when a directory is missing or a markdown file cannot be read.
pub fn build_page_index_only(
    skill_dirs: &[PathBuf],
    config: &PageIndexConfig,
) -> Result<SkillsIndex, String> {
    build_skills_index_with_options(skill_dirs, config, false, None, None)
}

/// Build or rebuild a chunk variant from existing node files under `entry_dir`.
///
/// # Errors
///
/// Returns an error when node files are missing or chunk generation fails.
pub fn build_chunk_variant(
    entry_dir: &Path,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
    config: &PageIndexConfig,
) -> Result<SkillsIndex, String> {
    let mut index = crate::skills_io::load_page_index_from_entry(entry_dir, doc_id)?;
    let doc = index
        .documents
        .get(doc_id)
        .cloned()
        .ok_or_else(|| format!("skill document not found: {doc_id}"))?;
    let mut structure = doc.structure.clone();
    populate_structure_text_from_node_files(&mut structure, &index, doc_id);

    attach_chunks_to_structure(
        &mut structure,
        config,
        &mut index,
        doc_id,
        pipeline,
        params_hash,
    )?;

    let mut updated = doc;
    updated.structure = structure.clone();
    index.documents.insert(doc_id.to_string(), updated);
    decompose_chunk_variant(&mut index, &structure, pipeline, params_hash);
    crate::skills_io::write_chunk_variant_files(&index, entry_dir, pipeline, params_hash)?;
    Ok(index)
}

fn build_skills_index_with_options(
    skill_dirs: &[PathBuf],
    config: &PageIndexConfig,
    include_chunks: bool,
    chunk_pipeline: Option<&str>,
    chunk_params_hash: Option<&str>,
) -> Result<SkillsIndex, String> {
    let mut index = SkillsIndex::default();

    for dir in skill_dirs {
        let expanded = expand_path(dir)?;
        if !expanded.is_dir() {
            return Err(format!(
                "skills directory not found: {}",
                expanded.display()
            ));
        }
        walk_skill_md_files(
            &expanded,
            &expanded,
            config,
            &mut index,
            include_chunks,
            chunk_pipeline,
            chunk_params_hash,
        )?;
    }

    Ok(index)
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

fn walk_skill_md_files(
    root: &Path,
    current: &Path,
    config: &PageIndexConfig,
    index: &mut SkillsIndex,
    include_chunks: bool,
    chunk_pipeline: Option<&str>,
    chunk_params_hash: Option<&str>,
) -> Result<(), String> {
    for entry in fs::read_dir(current).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.is_dir() {
            walk_skill_md_files(
                root,
                &path,
                config,
                index,
                include_chunks,
                chunk_pipeline,
                chunk_params_hash,
            )?;
            continue;
        }
        if path.extension().is_none_or(|e| e != "md") {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        let doc_id = doc_id_from_rel_path(&rel);
        if index.documents.contains_key(&doc_id) {
            continue;
        }

        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        let prefix = extract_skill_prefix(&content);
        let source_path = super::document_json::shorten_home_path(path.to_string_lossy().as_ref())?;
        let result = md_to_tree(&content, &source_path, config);
        let preamble = prefix.preamble.clone();
        let mut flat_for_decompose = build_flat_structure(
            &content,
            prefix.frontmatter.as_deref(),
            prefix.frontmatter_line_num,
            preamble.as_deref(),
            prefix.preamble_line_num,
            config,
        );
        if include_chunks {
            let pipeline = chunk_pipeline.unwrap_or("bm25");
            let params_hash = chunk_params_hash.unwrap_or("default");
            attach_chunks_to_structure(
                &mut flat_for_decompose,
                config,
                index,
                &doc_id,
                pipeline,
                params_hash,
            )?;
        }
        let doc = build_skill_document(
            doc_id.clone(),
            &source_path,
            &MdIndexResult {
                doc_name: result.doc_name,
                line_count: result.line_count,
                structure: flat_for_decompose.clone(),
            },
            config,
            prefix.frontmatter,
            preamble,
        );
        decompose_page_index(index, &doc, &flat_for_decompose);
        if include_chunks {
            let pipeline = chunk_pipeline.unwrap_or("bm25");
            let params_hash = chunk_params_hash.unwrap_or("default");
            decompose_chunk_variant(index, &doc.structure, pipeline, params_hash);
        }
        index.documents.insert(doc_id, doc);
    }
    Ok(())
}

fn build_flat_structure(
    markdown_content: &str,
    frontmatter: Option<&str>,
    frontmatter_line_num: Option<u32>,
    preamble: Option<&str>,
    preamble_line_num: Option<u32>,
    config: &PageIndexConfig,
) -> serde_json::Value {
    let (node_list, markdown_lines) = extract_nodes_from_markdown(markdown_content);
    let nodes_with_content = extract_node_text_content(&node_list, &markdown_lines);
    let tree = build_tree_from_nodes(&nodes_with_content);
    finalize_skill_structure(
        tree,
        frontmatter,
        frontmatter_line_num,
        preamble,
        preamble_line_num,
        config,
    )
}

/// Persist page-index files for one skill entry.
///
/// # Errors
///
/// Returns an error when files cannot be written.
pub fn write_page_index_entry(
    index: &SkillsIndex,
    entry_dir: &Path,
    doc_id: &str,
    extras: Option<&SkillDocumentExtras>,
) -> Result<(), String> {
    let doc = index
        .documents
        .get(doc_id)
        .ok_or_else(|| format!("skill document not found: {doc_id}"))?;
    write_page_index_files(entry_dir, doc, extras)?;
    crate::skills_io::write_node_files_from_index(index, entry_dir)?;
    Ok(())
}

/// Return whether the on-disk page index is complete for `content_sha256`.
#[must_use]
pub fn page_index_valid(entry_dir: &Path, content_sha256: &str) -> bool {
    let page_path = page_index_path(entry_dir);
    if !page_path.is_file() {
        return false;
    }
    let Ok(page) = read_document_json(&page_path) else {
        return false;
    };
    let stored_hash = page
        .get("content_sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !content_sha256.is_empty() && stored_hash != content_sha256 {
        return false;
    }
    let Some(structure) = page.get("structure") else {
        return false;
    };
    page_index_files_complete(entry_dir, structure)
}

/// Return whether a chunk variant directory is complete.
#[must_use]
pub fn chunk_variant_valid(
    entry_dir: &Path,
    pipeline: &str,
    params_hash: &str,
    doc_id: &str,
) -> bool {
    let variant_dir = chunk_variant_dir(entry_dir, pipeline, params_hash);
    let Ok(merged) = load_merged_document_from_entry(entry_dir, Some(&variant_dir)) else {
        return false;
    };
    let Some(doc) = SkillDocument::from_json(&merged) else {
        return false;
    };
    if doc.id != doc_id {
        return false;
    }
    super::document_json::chunk_variant_files_complete(
        entry_dir,
        pipeline,
        params_hash,
        &doc.structure,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stores_home_paths_with_tilde_prefix() -> Result<(), String> {
        let home = home_dir()?;
        let skills_dir =
            PathBuf::from(&home).join(format!(".cyt-skills-home-{}", std::process::id()));
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(skills_dir.join("example.md"), "# Example\n\nBody").map_err(|e| e.to_string())?;

        let index = build_skills_index(
            std::slice::from_ref(&skills_dir),
            &PageIndexConfig::default(),
        )?;
        let doc = index
            .documents
            .get("example")
            .ok_or_else(|| "missing example document".to_string())?;
        assert_eq!(
            doc.path,
            format!("~/.cyt-skills-home-{}/example.md", std::process::id())
        );

        let _ = fs::remove_dir_all(&skills_dir);
        Ok(())
    }

    #[test]
    fn page_index_only_skips_chunk_files() -> Result<(), String> {
        let home = home_dir()?;
        let skills_dir =
            PathBuf::from(&home).join(format!(".cyt-skills-page-only-{}", std::process::id()));
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("skill.md"),
            "# Root\n\nBody\n\n## Child\n\nMore",
        )
        .map_err(|e| e.to_string())?;

        let index = build_page_index_only(
            std::slice::from_ref(&skills_dir),
            &PageIndexConfig::default(),
        )?;
        assert!(index.files.keys().all(|k| !k.starts_with("chunks/")));

        let _ = fs::remove_dir_all(&skills_dir);
        Ok(())
    }
}
