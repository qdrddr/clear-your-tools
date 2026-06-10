use std::fs;
use std::path::Path;

use serde_json::Value;

use crate::pageindex::{SkillDocument, SkillsIndex};
use crate::pageindex::types::skills_decomposed_prefix;

const SKILLS_INDEX_FILE: &str = "skills_index.json";

/// Write decomposed skill files and a `skills_index.json` snapshot to `output_dir`.
///
/// # Errors
///
/// Returns an error when directories or files cannot be created or written.
pub fn write_skills_index(index: &SkillsIndex, output_dir: &Path) -> Result<(), String> {
    fs::create_dir_all(output_dir).map_err(|e| e.to_string())?;

    for (rel, content) in &index.files {
        let path = output_dir.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        fs::write(&path, content).map_err(|e| e.to_string())?;
    }

    let snapshot = serde_json::to_string_pretty(&index.to_skills_index_json())
        .map_err(|e| e.to_string())?;
    fs::write(output_dir.join(SKILLS_INDEX_FILE), snapshot).map_err(|e| e.to_string())?;
    Ok(())
}

/// Load a skills index from `skills_index.json` or reconstruct from decomposed files.
///
/// # Errors
///
/// Returns an error when the catalog directory is invalid or files cannot be read.
pub fn load_skills_index_from_dir(catalog_dir: &Path) -> Result<SkillsIndex, String> {
    let snapshot_path = catalog_dir.join(SKILLS_INDEX_FILE);
    if snapshot_path.is_file() {
        let raw = fs::read_to_string(&snapshot_path).map_err(|e| e.to_string())?;
        let val: Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
        let mut index = SkillsIndex::from_skills_index_json(&val)?;
        load_decomposed_files_into_index(catalog_dir, &mut index)?;
        return Ok(index);
    }
    SkillsIndex::from_decomposed_dir(catalog_dir)
}

/// Reconstruct a skills index from `skills/decomposed/` without a snapshot file.
///
/// # Errors
///
/// Returns an error when the decomposed directory is missing or contains no documents.
pub fn skills_index_from_decomposed_dir(dir: &Path) -> Result<SkillsIndex, String> {
    SkillsIndex::from_decomposed_dir(dir)
}

impl SkillsIndex {
    /// Reconstruct a skills index from decomposed files under `catalog_dir`.
    ///
    /// # Errors
    ///
    /// Returns an error when the decomposed directory is missing or contains no documents.
    pub fn from_decomposed_dir(catalog_dir: &Path) -> Result<Self, String> {
        let decomposed_root = catalog_dir.join(skills_decomposed_prefix());
        if !decomposed_root.is_dir() {
            return Err(format!(
                "skills decomposed directory not found: {}",
                decomposed_root.display()
            ));
        }

        let mut index = Self::default();
        load_decomposed_files_into_index(catalog_dir, &mut index)?;

        if index.documents.is_empty() {
            return Err("no skill documents found in decomposed directory".to_string());
        }
        Ok(index)
    }
}

/// Load decomposed skill files from `catalog_dir` into an existing index.
///
/// # Errors
///
/// Returns an error when decomposed files cannot be read or parsed.
pub fn load_decomposed_files_for_index(
    catalog_dir: &Path,
    index: &mut SkillsIndex,
) -> Result<(), String> {
    load_decomposed_files_into_index(catalog_dir, index)
}

fn load_decomposed_files_into_index(catalog_dir: &Path, index: &mut SkillsIndex) -> Result<(), String> {
    let decomposed_root = catalog_dir.join(skills_decomposed_prefix());
    if !decomposed_root.is_dir() {
        return Ok(());
    }

    for doc_entry in fs::read_dir(&decomposed_root).map_err(|e| e.to_string())? {
        let doc_entry = doc_entry.map_err(|e| e.to_string())?;
        let doc_dir = doc_entry.path();
        if !doc_dir.is_dir() {
            continue;
        }

        let doc_id = doc_dir
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();

        for file_entry in fs::read_dir(&doc_dir).map_err(|e| e.to_string())? {
            let file_entry = file_entry.map_err(|e| e.to_string())?;
            let path = file_entry.path();
            if !path.is_file() {
                continue;
            }

            let rel = path
                .strip_prefix(catalog_dir)
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .replace('\\', "/");

            let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
            index.files.insert(rel.clone(), content.clone());

            if path.file_name().and_then(|n| n.to_str()) == Some("document.json") {
                let val: Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;
                if let Some(doc) = SkillDocument::from_json(&val) {
                    index.documents.insert(doc_id.clone(), doc);
                }
            }
        }

        let chunks_dir = doc_dir.join("chunks");
        if chunks_dir.is_dir() {
            for file_entry in fs::read_dir(&chunks_dir).map_err(|e| e.to_string())? {
                let file_entry = file_entry.map_err(|e| e.to_string())?;
                let path = file_entry.path();
                if !path.is_file() {
                    continue;
                }
                let rel = path
                    .strip_prefix(catalog_dir)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
                index.files.insert(rel, content);
            }
        }
    }
    Ok(())
}

pub fn merge_skills_index_files(index: &mut SkillsIndex, other: &SkillsIndex) {
    index.documents.extend(other.documents.clone());
    index.files.extend(other.files.clone());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pageindex::{build_skills_index, PageIndexConfig};
    use std::fs;
    #[test]
    fn write_and_reconstruct_without_snapshot() -> Result<(), String> {
        let dir = std::env::temp_dir().join(format!("cyt-skills-{}", std::process::id()));
        let skills_dir = dir.join("skills-src");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("skill.md"),
            "# Root\n\nBody\n\n## Child\n\nMore",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_dir], &PageIndexConfig::default())?;
        write_skills_index(&index, &dir)?;

        let snapshot = dir.join(SKILLS_INDEX_FILE);
        fs::remove_file(&snapshot).map_err(|e| e.to_string())?;

        let rebuilt = SkillsIndex::from_decomposed_dir(&dir)?;
        assert_eq!(rebuilt.documents.len(), index.documents.len());
        assert!(!rebuilt.files.is_empty());
        let _ = fs::remove_dir_all(&dir);
        Ok(())
    }
}
