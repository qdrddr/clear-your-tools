use serde_json::Value;
use std::path::{Component, Path, PathBuf};
use std::sync::{OnceLock, RwLock};

/// SDK runtime defaults (paths + catalog I/O); override from the host app via `configure`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PathConfig {
    pub json_ext: String,
    pub md_ext: String,
    pub decomposed_prefix: String,
    pub decomposed_root: PathBuf,
    pub catalog_prefix: String,
    pub builder_memory_only: bool,
    pub default_catalog_dir: PathBuf,
    pub write_catalog_prune: bool,
}

impl Default for PathConfig {
    fn default() -> Self {
        Self {
            json_ext: ".json".to_string(),
            md_ext: ".md".to_string(),
            decomposed_prefix: "schemas/decomposed/".to_string(),
            decomposed_root: PathBuf::from("schemas/decomposed"),
            catalog_prefix: "catalog".to_string(),
            builder_memory_only: false,
            default_catalog_dir: PathBuf::from("catalog"),
            write_catalog_prune: true,
        }
    }
}

fn config_lock() -> &'static RwLock<PathConfig> {
    static CONFIG: OnceLock<RwLock<PathConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| RwLock::new(PathConfig::default()))
}

pub fn configure(cfg: PathConfig) {
    *config_lock().write().expect("path config lock") = cfg;
}

pub fn snapshot() -> PathConfig {
    config_lock().read().expect("path config lock").clone()
}

pub fn json_ext() -> String {
    snapshot().json_ext
}

pub fn md_ext() -> String {
    snapshot().md_ext
}

pub fn decomposed_prefix() -> String {
    snapshot().decomposed_prefix
}

pub fn decomposed_root() -> PathBuf {
    snapshot().decomposed_root
}

pub fn catalog_prefix() -> String {
    snapshot().catalog_prefix
}

pub fn builder_memory_only() -> bool {
    snapshot().builder_memory_only
}

pub fn default_catalog_dir() -> PathBuf {
    snapshot().default_catalog_dir
}

pub fn write_catalog_prune() -> bool {
    snapshot().write_catalog_prune
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
    let cfg = snapshot();
    let rel = if let Some(stripped) = rel_path.strip_prefix(&cfg.decomposed_prefix) {
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
    if first.ends_with(&cfg.json_ext) {
        first.trim_end_matches(&cfg.json_ext).to_string()
    } else {
        first.into_owned()
    }
}

pub fn get_root_tool_key(file_path: &str) -> Option<String> {
    let cfg = snapshot();
    let key = to_decomposed_key(file_path)?;
    let root = cfg.decomposed_root.clone();
    let rel = Path::new(&key).strip_prefix(&root).ok()?;
    if rel.as_os_str().is_empty() {
        return None;
    }
    let parts: Vec<_> = rel.components().collect();
    if parts.len() == 1 {
        let name = parts[0].as_os_str().to_string_lossy();
        if name.ends_with(&cfg.json_ext) {
            return Some(key);
        }
    }
    let tool_id = parts[0].as_os_str().to_string_lossy();
    Some(format!(
        "{}{}{}",
        cfg.decomposed_prefix, tool_id, cfg.json_ext
    ))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_prefix_round_trip() {
        let cfg = PathConfig::default();
        configure(cfg.clone());
        let rel = format!("{}tool.json", decomposed_prefix());
        assert_eq!(tool_id_from_decomposed_rel(&rel), "tool");
    }
}
