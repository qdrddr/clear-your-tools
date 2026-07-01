use crate::build::CatalogIndex;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

/// Resolve output directory and prune flag from optional overrides and path config.
pub fn resolve_write_catalog_paths(
    output_dir: Option<&Path>,
    prune: Option<bool>,
) -> (PathBuf, bool) {
    let cfg = crate::paths::snapshot();
    let dir = output_dir
        .map(Path::to_path_buf)
        .unwrap_or(cfg.default_catalog_dir);
    let prune = prune.unwrap_or(cfg.write_catalog_prune);
    (dir, prune)
}

/// Write catalog index using optional output dir / prune (defaults from [`crate::paths`]).
///
/// # Errors
/// Returns an error string when the output directory cannot be created or catalog files cannot be written.
pub fn write_catalog_index_resolved(
    index: &CatalogIndex,
    output_dir: Option<&Path>,
    prune: Option<bool>,
) -> Result<(), String> {
    let (dir, prune) = resolve_write_catalog_paths(output_dir, prune);
    write_catalog_index(index, &dir, prune)
}

/// Write catalog index files under `output_dir`, optionally pruning stale entries.
///
/// # Errors
/// Returns an error string when the output directory cannot be created, files cannot be written, or pruning fails.
pub fn write_catalog_index(
    index: &CatalogIndex,
    output_dir: &Path,
    prune: bool,
) -> Result<(), String> {
    let root = output_dir;
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let schemas = root.join("schemas");
    fs::create_dir_all(&schemas).map_err(|e| e.to_string())?;

    let mut output_map: HashMap<PathBuf, String> = HashMap::new();
    for (rel_path, content) in &index.files {
        output_map.insert(root.join(rel_path), content.clone());
    }

    apply_outputs(&output_map)?;
    if prune {
        let expected: HashSet<PathBuf> = output_map.keys().cloned().collect();
        prune_stale_files(root, &expected)?;
    }
    Ok(())
}

fn apply_outputs(output_map: &HashMap<PathBuf, String>) -> Result<(), String> {
    for (path, content) in output_map {
        if path.exists()
            && let Ok(existing) = fs::read_to_string(path)
            && existing == *content
        {
            continue;
        }
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        fs::write(path, content).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn should_skip_hidden(path: &Path, root: &Path) -> bool {
    path.strip_prefix(root).ok().is_some_and(|rel| {
        rel.components()
            .any(|c| c.as_os_str().to_string_lossy().starts_with('.'))
    })
}

fn prune_stale_files(root: &Path, expected_paths: &HashSet<PathBuf>) -> Result<(), String> {
    if !root.exists() {
        return Ok(());
    }

    let mut all_paths: Vec<PathBuf> = Vec::new();
    collect_paths(root, &mut all_paths)?;

    for path in &all_paths {
        if should_skip_hidden(path, root) {
            continue;
        }
        if path.is_file() && !expected_paths.contains(path) {
            fs::remove_file(path).map_err(|e| e.to_string())?;
        }
    }

    all_paths.sort_by_key(|p| std::cmp::Reverse(p.components().count()));
    for path in all_paths {
        if should_skip_hidden(&path, root) {
            continue;
        }
        if path.is_dir() && path.read_dir().is_ok_and(|mut d| d.next().is_none()) {
            let _ = fs::remove_dir(path);
        }
    }
    Ok(())
}

fn collect_paths(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
    if !dir.is_dir() {
        return Ok(());
    }
    for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        out.push(path.clone());
        if path.is_dir() {
            collect_paths(&path, out)?;
        }
    }
    Ok(())
}
