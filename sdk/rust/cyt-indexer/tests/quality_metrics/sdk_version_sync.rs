//! Cross-language SDK version alignment gate.

use std::fs;
use std::path::PathBuf;

fn repo_root() -> Result<PathBuf, String> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .map_err(|err| format!("repo root: {err}"))
}

fn read_version_from_toml(path: &PathBuf, key: &str) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix(&format!("{key} = ")) {
            return rest.trim_matches('"').to_string().into();
        }
    }
    None
}

fn read_version_from_json(path: &PathBuf) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&text).ok()?;
    parsed
        .get("version")
        .and_then(serde_json::Value::as_str)
        .map(str::to_string)
}

#[test]
fn rust_cargo_version_matches_python_and_typescript_sdks() -> Result<(), String> {
    let root = repo_root()?;
    let rust_version = env!("CARGO_PKG_VERSION");
    let python_version = read_version_from_toml(&root.join("sdk/python/pyproject.toml"), "version")
        .ok_or_else(|| "sdk/python/pyproject.toml version".to_string())?;
    let typescript_version = read_version_from_json(&root.join("sdk/typescript/package.json"))
        .ok_or_else(|| "typescript version".to_string())?;
    assert_eq!(rust_version, python_version.as_str());
    assert_eq!(rust_version, typescript_version.as_str());
    Ok(())
}
