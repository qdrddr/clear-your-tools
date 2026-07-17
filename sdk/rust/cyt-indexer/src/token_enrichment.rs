//! Tiktoken-backed token counts for tool catalog metadata and catalog dict entries.

use std::collections::HashMap;
use std::hash::BuildHasher;

use chunk_your_tools::paths::{decomposed_prefix, json_ext, md_ext};
use serde_json::{Value, json};

use crate::paths;
use crate::tiktoken;

const FULL_METADATA_REL: &str = "schemas/full/metadata.json";
const DECOMPOSED_METADATA_REL: &str = "schemas/decomposed/metadata.json";

fn catalog_file_token_count(rel_path: &str, content: &str) -> usize {
    if rel_path.ends_with(&json_ext()) {
        serde_json::from_str::<Value>(content)
            .ok()
            .and_then(|value| tiktoken::count_json_tokens(&value).ok())
            .unwrap_or_else(|| tiktoken::count_tokens_or_min(content))
    } else {
        tiktoken::count_tokens_or_min(content)
    }
}

fn serialize_metadata_json(value: &Value) -> String {
    let mut serialized = serde_json::to_string_pretty(value).unwrap_or_default();
    serialized.push('\n');
    serialized
}

fn decomposed_metadata_entry_type(rel_path: &str) -> &'static str {
    if rel_path.ends_with(&md_ext()) {
        return "enum";
    }
    let rest = rel_path
        .strip_prefix(&decomposed_prefix())
        .unwrap_or(rel_path);
    if rest.contains('/') {
        "property"
    } else {
        "tool"
    }
}

/// Replace null/placeholder token counts in catalog file metadata with tiktoken counts.
pub fn enrich_tool_schema_metadata<S: BuildHasher>(files: &mut HashMap<String, String, S>) {
    let full_prefix = "schemas/full/";
    let mut full_entries: Vec<Value> = files
        .iter()
        .filter(|(rel, _)| rel.starts_with(full_prefix) && rel.ends_with(&json_ext()))
        .map(|(rel, content)| {
            json!({
                "file_path": rel,
                "token_count": catalog_file_token_count(rel, content),
            })
        })
        .collect();
    full_entries.sort_by(|a, b| {
        a.get("file_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(b.get("file_path").and_then(Value::as_str).unwrap_or(""))
    });
    if !full_entries.is_empty() {
        let metadata = if full_entries.len() == 1 {
            full_entries.into_iter().next().unwrap_or(Value::Null)
        } else {
            json!({ "files": full_entries })
        };
        files.insert(
            FULL_METADATA_REL.to_string(),
            serialize_metadata_json(&metadata),
        );
    }

    let decomposed_prefix = decomposed_prefix();
    let mut decomposed_entries: Vec<Value> = files
        .iter()
        .filter(|(rel, _)| {
            rel.starts_with(&decomposed_prefix)
                && *rel != DECOMPOSED_METADATA_REL
                && (rel.ends_with(&json_ext()) || rel.ends_with(&md_ext()))
        })
        .map(|(rel, content)| {
            let mut entry = json!({
                "file_path": rel,
                "token_count": catalog_file_token_count(rel, content),
            });
            if let Some(obj) = entry.as_object_mut() {
                obj.insert("type".into(), json!(decomposed_metadata_entry_type(rel)));
            }
            entry
        })
        .collect();
    decomposed_entries.sort_by(|a, b| {
        a.get("file_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(b.get("file_path").and_then(Value::as_str).unwrap_or(""))
    });
    if !decomposed_entries.is_empty() {
        files.insert(
            DECOMPOSED_METADATA_REL.to_string(),
            serialize_metadata_json(&Value::Array(decomposed_entries)),
        );
    }
}

fn decomposed_file_token_counts<S: BuildHasher>(
    files: &HashMap<String, String, S>,
) -> HashMap<String, usize> {
    let Some(raw) = files.get(DECOMPOSED_METADATA_REL) else {
        return HashMap::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(raw) else {
        return HashMap::new();
    };
    let entries = value
        .as_array()
        .or_else(|| value.get("files").and_then(Value::as_array));
    let Some(entries) = entries else {
        return HashMap::new();
    };
    let mut map = HashMap::new();
    for entry in entries {
        let Some(obj) = entry.as_object() else {
            continue;
        };
        let Some(file_path) = obj.get("file_path").and_then(Value::as_str) else {
            continue;
        };
        let token_count = obj
            .get("token_count")
            .and_then(Value::as_u64)
            .and_then(|n| usize::try_from(n).ok())
            .unwrap_or_else(|| {
                files
                    .get(file_path)
                    .map_or(0, |content| catalog_file_token_count(file_path, content))
            });
        map.insert(file_path.to_string(), token_count);
    }
    map
}

fn json_insert_token_count(entry: &mut Value, token_count: usize) {
    if let Some(obj) = entry.as_object_mut() {
        obj.insert("token_count".into(), json!(token_count));
    }
}

fn path_stem(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

/// Build catalog dict JSON with tiktoken-filled `token_count` fields.
#[must_use]
pub fn catalog_dict_with_tokens<S: BuildHasher>(
    files: &HashMap<String, String, S>,
    tools: &[Value],
    catalog_prefix: &str,
) -> Value {
    let token_counts = decomposed_file_token_counts(files);
    let mut md_entries = Vec::new();
    let mut json_entries = Vec::new();
    let mut paths: Vec<_> = files.keys().cloned().collect();
    paths.sort();

    for rel_path in paths {
        if !rel_path.starts_with(&decomposed_prefix()) {
            continue;
        }
        let content = &files[&rel_path];
        let file_path = format!("{catalog_prefix}/{rel_path}");
        if rel_path.ends_with(&md_ext()) {
            let id = path_stem(&rel_path);
            let mut entry = json!({
                "id": id,
                "file_path": file_path,
                "score": 1.0,
                "start_line": 1,
                "end_line": 1,
                "language": "markdown",
                "content": content,
            });
            if let Some(token_count) = token_counts.get(rel_path.as_str()) {
                json_insert_token_count(&mut entry, *token_count);
            }
            md_entries.push(entry);
        } else if rel_path.ends_with(&json_ext()) {
            let Ok(parsed) = serde_json::from_str::<Value>(content) else {
                continue;
            };
            if !parsed.is_object() {
                continue;
            }
            let line_count = content.lines().count();
            let entry_id = parsed
                .get("id")
                .cloned()
                .unwrap_or_else(|| Value::String(paths::tool_id_from_decomposed_rel(&rel_path)));
            let mut entry = json!({
                "id": entry_id,
                "name": entry_id,
                "file_path": file_path,
                "score": 1.0,
                "start_line": 1,
                "end_line": line_count,
                "language": "json",
                "content": parsed,
            });
            if let Some(token_count) = token_counts.get(rel_path.as_str()) {
                json_insert_token_count(&mut entry, *token_count);
            }
            json_entries.push(entry);
        }
    }
    json!({
        "md": md_entries,
        "json": json_entries,
        "tools": tools,
    })
}

/// Fill null `token_count` values inside an existing catalog dict in place.
pub fn enrich_catalog_dict_token_counts<S: BuildHasher>(
    dict: &mut Value,
    files: &HashMap<String, String, S>,
) {
    let token_counts = decomposed_file_token_counts(files);
    for key in ["md", "json"] {
        let Some(items) = dict.get_mut(key).and_then(Value::as_array_mut) else {
            continue;
        };
        for item in items {
            let Some(obj) = item.as_object_mut() else {
                continue;
            };
            let needs_count = obj
                .get("token_count")
                .is_none_or(serde_json::Value::is_null);
            if !needs_count {
                continue;
            }
            let Some(file_path) = obj.get("file_path").and_then(Value::as_str) else {
                continue;
            };
            let rel = file_path
                .split_once('/')
                .map_or(file_path, |(_, rest)| rest);
            let count = token_counts
                .get(rel)
                .copied()
                .or_else(|| files.get(rel).map(|c| catalog_file_token_count(rel, c)))
                .unwrap_or(0);
            obj.insert("token_count".into(), json!(count));
        }
    }
    if let Some(map) = dict.as_object_mut() {
        map.entry("tools".to_string())
            .or_insert_with(|| Value::Array(Vec::new()));
    }
}
