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
    pub skills_decomposed_prefix: String,
    pub skills_decomposed_root: PathBuf,
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
            skills_decomposed_prefix: "skills/decomposed/".to_string(),
            skills_decomposed_root: PathBuf::from("skills/decomposed"),
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
    *config_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = cfg;
}

#[must_use]
pub fn snapshot() -> PathConfig {
    config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

#[must_use]
pub fn json_ext() -> String {
    snapshot().json_ext
}

#[must_use]
pub fn md_ext() -> String {
    snapshot().md_ext
}

#[must_use]
pub fn decomposed_prefix() -> String {
    snapshot().decomposed_prefix
}

#[must_use]
pub fn decomposed_root() -> PathBuf {
    snapshot().decomposed_root
}

#[must_use]
pub fn catalog_prefix() -> String {
    snapshot().catalog_prefix
}

#[must_use]
pub fn builder_memory_only() -> bool {
    snapshot().builder_memory_only
}

#[must_use]
pub fn default_catalog_dir() -> PathBuf {
    snapshot().default_catalog_dir
}

#[must_use]
pub fn write_catalog_prune() -> bool {
    snapshot().write_catalog_prune
}

#[must_use]
pub fn skills_decomposed_prefix() -> String {
    snapshot().skills_decomposed_prefix
}

#[must_use]
pub fn to_skills_decomposed_key(file_path: &str) -> Option<String> {
    let parts: Vec<_> = Path::new(file_path).components().collect();
    for i in 0..parts.len().saturating_sub(1) {
        if parts[i] == Component::Normal("skills".as_ref())
            && parts[i + 1] == Component::Normal("decomposed".as_ref())
        {
            let sub: PathBuf = parts[i..].iter().collect();
            return Some(sub.to_string_lossy().into_owned());
        }
    }
    None
}

#[must_use]
pub fn is_catalog_decomposed_path(file_path: &str) -> bool {
    to_decomposed_key(file_path).is_some() || to_skills_decomposed_key(file_path).is_some()
}

#[must_use]
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

#[must_use]
pub fn tool_id_from_decomposed_rel(rel_path: &str) -> String {
    let cfg = snapshot();
    let rel = rel_path
        .strip_prefix(&cfg.decomposed_prefix)
        .map_or(rel_path, |stripped| stripped);
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

#[must_use]
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

/// User home directory from `HOME` or `USERPROFILE`.
///
/// # Errors
///
/// Returns an error when neither variable is set.
pub fn home_dir() -> Result<PathBuf, String> {
    for key in ["HOME", "USERPROFILE"] {
        if let Ok(value) = std::env::var(key)
            && !value.is_empty()
        {
            return Ok(PathBuf::from(value));
        }
    }
    Err("home directory not found (HOME/USERPROFILE unset)".to_string())
}

#[must_use]
pub fn normalize_path_separators(path: &str) -> String {
    path.replace('\\', "/")
}

/// Expand `~/…` prefixes against [`home_dir`].
///
/// # Errors
///
/// Returns an error when tilde expansion requires home resolution and it fails.
pub fn expand_home_path(path: &Path) -> Result<PathBuf, String> {
    let s = path.to_string_lossy();
    if s == "~" {
        return home_dir();
    }
    if let Some(stripped) = s.strip_prefix("~/") {
        return Ok(home_dir()?.join(stripped));
    }
    Ok(path.to_path_buf())
}

/// Rewrite absolute paths under the user's home as `~/…`.
///
/// # Errors
///
/// Returns an error when home resolution or path expansion fails.
pub fn shorten_home_path(path: &str) -> Result<String, String> {
    let expanded = expand_home_path(Path::new(path))?;
    let home = normalize_path_separators(&home_dir()?.to_string_lossy());
    let path_str = normalize_path_separators(&expanded.to_string_lossy());
    if path_str == home {
        return Ok("~".to_string());
    }
    let home_prefix = format!("{home}/");
    if let Some(rest) = path_str.strip_prefix(&home_prefix) {
        return Ok(format!("~/{rest}"));
    }
    Ok(path_str)
}

#[must_use]
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
        configure(cfg);
        let rel = format!("{}tool.json", decomposed_prefix());
        assert_eq!(tool_id_from_decomposed_rel(&rel), "tool");
    }

    #[test]
    fn shorten_home_path_normalizes_separators() -> Result<(), String> {
        let home = home_dir()?;
        let home_norm = normalize_path_separators(&home.to_string_lossy());
        let nested = format!("{home_norm}/.cyt-test/example.md");
        let with_backslashes = nested.replace('/', "\\");
        assert_eq!(
            shorten_home_path(&with_backslashes)?,
            "~/.cyt-test/example.md"
        );
        Ok(())
    }
}
