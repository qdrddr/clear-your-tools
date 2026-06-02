use serde_json::Value;
use std::path::{Component, Path, PathBuf};

pub const JSON_EXT: &str = ".json";
pub const MD_EXT: &str = ".md";
pub const DECOMPOSED_PREFIX: &str = "schemas/decomposed/";

pub fn decomposed_root() -> PathBuf {
    PathBuf::from("schemas/decomposed")
}

pub fn to_decomposed_key(file_path: &str) -> Option<String> {
    let parts: Vec<_> = Path::new(file_path).components().collect();
    for i in 0..parts.len().saturating_sub(1) {
        if parts[i] == Component::Normal("schemas".as_ref())
            && parts[i + 1] == Component::Normal("decomposed".as_ref())
        {
            let sub: PathBuf = parts[i..].iter().collect();
            return Some(sub.to_string_lossy().into_owned());
        }
    }
    None
}

pub fn tool_id_from_decomposed_rel(rel_path: &str) -> String {
    let rel = if let Some(stripped) = rel_path.strip_prefix(DECOMPOSED_PREFIX) {
        stripped
    } else {
        rel_path
    };
    let path = Path::new(rel);
    let parts: Vec<_> = path.components().collect();
    if parts.is_empty() {
        return path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
    }
    let first = parts[0].as_os_str().to_string_lossy();
    if first.ends_with(JSON_EXT) {
        first.trim_end_matches(JSON_EXT).to_string()
    } else {
        first.into_owned()
    }
}

pub fn get_root_tool_key(file_path: &str) -> Option<String> {
    let key = to_decomposed_key(file_path)?;
    let root = decomposed_root();
    let rel = Path::new(&key).strip_prefix(&root).ok()?;
    if rel.as_os_str().is_empty() {
        return None;
    }
    let parts: Vec<_> = rel.components().collect();
    if parts.len() == 1 {
        let name = parts[0].as_os_str().to_string_lossy();
        if name.ends_with(JSON_EXT) {
            return Some(key);
        }
    }
    let tool_id = parts[0].as_os_str().to_string_lossy();
    Some(format!("schemas/decomposed/{tool_id}{JSON_EXT}"))
}

pub fn collect_enums(schema: &Value) -> Vec<Value> {
    let mut found = Vec::new();
    collect_enums_inner(schema, &mut found);
    found
}

fn collect_enums_inner(node: &Value, found: &mut Vec<Value>) {
    match node {
        Value::Object(map) => {
            if let Some(Value::Array(items)) = map.get("enum") {
                found.extend(items.iter().cloned());
            }
            for val in map.values() {
                if val.is_object() || val.is_array() {
                    collect_enums_inner(val, found);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                if item.is_object() || item.is_array() {
                    collect_enums_inner(item, found);
                }
            }
        }
        _ => {}
    }
}
