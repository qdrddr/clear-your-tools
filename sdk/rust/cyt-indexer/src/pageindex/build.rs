use std::fs;
use std::path::{Path, PathBuf};

use super::config::PageIndexConfig;
use super::decompose::{attach_chunks_to_structure, decompose_document};
use super::index::md_to_tree;
use super::parse::{extract_node_text_content, extract_nodes_from_markdown, extract_skill_prefix};
use super::tree::{build_tree_from_nodes, finalize_skill_structure};
use super::types::{MdIndexResult, SkillsIndex, build_skill_document, doc_id_from_rel_path};

/// Build an in-memory skills index from one or more skill directories.
///
/// # Errors
///
/// Returns an error when a directory is missing or a markdown file cannot be read.
pub fn build_skills_index(
    skill_dirs: &[PathBuf],
    config: &PageIndexConfig,
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
        walk_skill_md_files(&expanded, &expanded, config, &mut index)?;
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

/// Store paths under `$HOME` as `~/...` in the skills index snapshot.
fn shorten_home_path(path: &Path) -> Result<String, String> {
    let home = home_dir()?;
    let path_str = path.to_string_lossy().replace('\\', "/");
    if path_str == home {
        return Ok("~".to_string());
    }
    let home_prefix = format!("{home}/");
    if let Some(rest) = path_str.strip_prefix(&home_prefix) {
        return Ok(format!("~/{rest}"));
    }
    Ok(path_str)
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
        let prefix = extract_skill_prefix(&content);
        let source_path = shorten_home_path(&path)?;
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
        attach_chunks_to_structure(&mut flat_for_decompose, config, index, &doc_id)?;
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
        decompose_document(index, &doc, &flat_for_decompose, config);
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
    fn expands_tilde_skill_dir_input() -> Result<(), String> {
        let home = home_dir()?;
        let rel = format!(".cyt-skills-tilde-{}", std::process::id());
        let skills_dir = PathBuf::from(&home).join(&rel);
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(skills_dir.join("skill.md"), "# Skill\n\nBody").map_err(|e| e.to_string())?;

        let skills_input = PathBuf::from(format!("~/{rel}"));
        let index = build_skills_index(
            std::slice::from_ref(&skills_input),
            &PageIndexConfig::default(),
        )?;
        let doc = index
            .documents
            .get("skill")
            .ok_or_else(|| "missing skill document".to_string())?;
        assert_eq!(doc.path, format!("~/{rel}/skill.md"));

        let _ = fs::remove_dir_all(&skills_dir);
        Ok(())
    }
}
