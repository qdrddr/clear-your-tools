use std::fs;
use std::path::{Path, PathBuf};

use super::config::PageIndexConfig;
use super::decompose::decompose_document;
use super::index::md_to_tree;
use super::parse::{extract_node_text_content, extract_nodes_from_markdown};
use super::tree::{build_tree_from_nodes, format_structure_for_output, write_node_id};
use super::types::{build_skill_document, doc_id_from_rel_path, SkillsIndex};

/// Build an in-memory skills index from one or more skill directories.
///
/// # Errors
///
/// Returns an error when a directory is missing or a markdown file cannot be read.
pub fn build_skills_index(skill_dirs: &[PathBuf], config: &PageIndexConfig) -> Result<SkillsIndex, String> {
    let mut index = SkillsIndex::default();

    for dir in skill_dirs {
        let expanded = expand_path(dir)?;
        if !expanded.is_dir() {
            return Err(format!("skills directory not found: {}", expanded.display()));
        }
        walk_skill_md_files(&expanded, &expanded, config, &mut index)?;
    }

    Ok(index)
}

fn expand_path(path: &Path) -> Result<PathBuf, String> {
    let s = path.to_string_lossy();
    if let Some(stripped) = s.strip_prefix("~/") {
        let home = std::env::var("HOME").map_err(|_| "HOME not set for ~ path".to_string())?;
        return Ok(PathBuf::from(home).join(stripped));
    }
    Ok(path.to_path_buf())
}

fn walk_skill_md_files(
    root: &Path,
    current: &Path,
    config: &PageIndexConfig,
    index: &mut SkillsIndex,
) -> Result<(), String> {
    for entry in fs::read_dir(current).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.is_dir() {
            walk_skill_md_files(root, &path, config, index)?;
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
        let result = md_to_tree(&content, path.to_string_lossy().as_ref(), config);

        let doc = build_skill_document(doc_id.clone(), &path.to_string_lossy(), &result, config);
        let flat_for_decompose = build_flat_structure(&content, config);
        decompose_document(index, &doc, &flat_for_decompose);
        index.documents.insert(doc_id, doc);
    }
    Ok(())
}

fn build_flat_structure(markdown_content: &str, config: &PageIndexConfig) -> serde_json::Value {
    let (node_list, markdown_lines) = extract_nodes_from_markdown(markdown_content);
    let nodes_with_content = extract_node_text_content(&node_list, &markdown_lines);
    let mut tree = build_tree_from_nodes(&nodes_with_content);
    if config.if_add_node_id {
        write_node_id(&mut tree, 0);
    }
    format_structure_for_output(
        &tree,
        &PageIndexConfig {
            if_add_node_id: config.if_add_node_id,
            if_add_node_text: true,
        },
    )
}
