use std::path::Path;

use serde_json::{json, Map, Value};
use std::collections::HashMap;

use super::config::PageIndexConfig;

#[derive(Debug, Clone)]
pub struct MdIndexResult {
    pub doc_name: String,
    pub line_count: u32,
    pub structure: Value,
}

#[derive(Debug, Clone)]
pub struct SkillDocument {
    pub id: String,
    pub doc_type: String,
    pub path: String,
    pub doc_name: String,
    pub line_count: u32,
    pub structure: Value,
    /// YAML frontmatter captured at catalog build time (`name`, `description`, etc.).
    pub frontmatter: Option<String>,
    /// Body text between frontmatter and the first heading, when present.
    pub preamble: Option<String>,
}

impl SkillDocument {
    #[must_use]
    pub fn to_json(&self) -> Value {
        let mut obj = json!({
            "id": self.id,
            "type": self.doc_type,
            "path": self.path,
            "doc_name": self.doc_name,
            "line_count": self.line_count,
            "structure": self.structure,
        });
        if let Some(map) = obj.as_object_mut() {
            if let Some(frontmatter) = &self.frontmatter {
                map.insert("frontmatter".to_string(), Value::String(frontmatter.clone()));
            }
            if let Some(preamble) = &self.preamble {
                map.insert("preamble".to_string(), Value::String(preamble.clone()));
            }
        }
        obj
    }

    #[must_use]
    pub fn from_json(val: &Value) -> Option<Self> {
        let obj = val.as_object()?;
        let line_count = obj
            .get("line_count")
            .and_then(serde_json::Value::as_u64)
            .map_or(0, |n| u32::try_from(n).unwrap_or(0));
        Some(Self {
            id: obj.get("id")?.as_str()?.to_string(),
            doc_type: obj
                .get("type")
                .and_then(|v| v.as_str())
                .unwrap_or("md")
                .to_string(),
            path: obj.get("path").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            doc_name: obj
                .get("doc_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            line_count,
            structure: obj.get("structure").cloned().unwrap_or(Value::Array(vec![])),
            frontmatter: obj
                .get("frontmatter")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            preamble: obj
                .get("preamble")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        })
    }
}

#[derive(Debug, Clone, Default)]
pub struct SkillsIndex {
    pub documents: HashMap<String, SkillDocument>,
    pub files: HashMap<String, String>,
}

impl SkillsIndex {
    #[must_use]
    pub fn to_skills_index_json(&self) -> Value {
        let mut docs = Map::new();
        let mut keys: Vec<_> = self.documents.keys().collect();
        keys.sort();
        for key in keys {
            if let Some(doc) = self.documents.get(key) {
                docs.insert(key.clone(), doc.to_json());
            }
        }
        json!({ "documents": Value::Object(docs) })
    }

    #[must_use]
    pub fn to_skills_dict(&self) -> Value {
        let mut md_entries = Vec::new();
        let mut paths: Vec<_> = self.files.keys().collect();
        paths.sort();
        for rel_path in paths {
            let content = &self.files[rel_path];
            let path = Path::new(rel_path);
            let is_node_md = path
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("md"))
                && !rel_path.contains("/chunks/");
            let is_chunk_md = rel_path.contains("/chunks/") && path.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("md"));
            if (!is_node_md && !is_chunk_md) || rel_path.ends_with("document.json") {
                continue;
            }
            let id = path
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned();
            let mut entry = json!({
                "id": id,
                "file_path": rel_path,
                "score": 1.0,
                "start_line": 1,
                "end_line": 1,
                "language": "markdown",
                "content": content,
            });
            if is_chunk_md
                && let Some(parent) = path.parent().and_then(|p| p.parent())
                && let Some(doc_id) = parent.file_name()
                && let Some(entry_obj) = entry.as_object_mut()
            {
                entry_obj.insert(
                    "doc_id".to_string(),
                    Value::String(doc_id.to_string_lossy().into_owned()),
                );
            }
            md_entries.push(entry);
        }
        json!({ "md": md_entries })
    }

    #[must_use]
    pub fn documents_as_json(&self) -> Value {
        let mut docs = Map::new();
        for (k, v) in &self.documents {
            docs.insert(k.clone(), v.to_json());
        }
        Value::Object(docs)
    }

    /// Load a skills index from a `{ "documents": ... }` JSON value.
    ///
    /// # Errors
    ///
    /// Returns an error when `documents` is missing or malformed.
    pub fn from_skills_index_json(val: &Value) -> Result<Self, String> {
        let mut index = Self::default();
        let docs = val
            .get("documents")
            .and_then(|v| v.as_object())
            .ok_or_else(|| "skills index missing documents object".to_string())?;
        for (doc_id, doc_val) in docs {
            let doc = SkillDocument::from_json(doc_val)
                .ok_or_else(|| format!("invalid document entry for {doc_id}"))?;
            index.documents.insert(doc_id.clone(), doc);
        }
        Ok(index)
    }
}

#[must_use]
pub fn doc_id_from_rel_path(rel_path: &str) -> String {
    let normalized = rel_path.replace('\\', "/");
    let without_ext = normalized.strip_suffix(".md").unwrap_or(&normalized);
    without_ext.replace('/', "__").to_ascii_lowercase()
}

#[must_use]
pub const fn skills_decomposed_prefix() -> &'static str {
    "skills/decomposed/"
}

#[must_use]
pub fn document_json_rel(doc_id: &str) -> String {
    format!("{}{doc_id}/document.json", skills_decomposed_prefix())
}

#[must_use]
pub fn node_md_rel(doc_id: &str, node_id: u32) -> String {
    format!("{}{doc_id}/{node_id}.md", skills_decomposed_prefix())
}

#[must_use]
pub fn chunk_md_rel(doc_id: &str, chunk_id: u32) -> String {
    format!("{}{doc_id}/chunks/{chunk_id}.md", skills_decomposed_prefix())
}

#[must_use]
pub fn build_skill_document(
    doc_id: String,
    source_path: &str,
    result: &MdIndexResult,
    config: &PageIndexConfig,
    frontmatter: Option<String>,
    preamble: Option<String>,
) -> SkillDocument {
    SkillDocument {
        id: doc_id,
        doc_type: "md".to_string(),
        path: source_path.to_string(),
        doc_name: result.doc_name.clone(),
        line_count: result.line_count,
        structure: super::tree::format_structure_for_output(&result.structure, config),
        frontmatter,
        preamble,
    }
}
