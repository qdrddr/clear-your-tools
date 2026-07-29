//! Tool catalog disk cache under ``~/.config/cyt/tools/entries/{tool_content_hash}/``.
//!
//! Each MCP tool is cached independently; the directory name is a SHA-256 of the
//! original (not-yet-decomposed) tool definition only — no policy fingerprint.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::build::{CatalogIndex, build_catalog_index, catalog_index_to_catalog_dict};
use crate::catalog_io::write_catalog_index;
use crate::paths::{collect_enums, decomposed_prefix, json_ext};
use crate::tool_entries::anthropic_tools_to_catalog_entries;

use super::disk_writer::maybe_enqueue_tool_catalog;
use super::hot::{get_tool_catalog, store_tool_catalog};
use super::lock::BuildLock;
use super::manifest::CacheStatus;
use super::{CachePolicy, CacheResult, disk_available, expand_tilde};

const TOOL_DEF_HASH_PREFIX: &[u8] = b"v1-tool-def\0";
const SKIP_CACHE_FILES: &[&str] = &["manifest.json", "tools.json"];

#[derive(Debug, Clone)]
pub struct ToolCatalogHandle {
    pub catalog: Value,
    pub index: CatalogIndex,
    pub entry_dir: PathBuf,
    pub content_hash: String,
    pub disk_backed: bool,
    pub cache_status: CacheStatus,
}

/// SHA-256 hex digest of a single original tool definition (Anthropic API shape or catalog ``full_schema``).
#[must_use]
pub fn tool_definition_content_hash(definition: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(TOOL_DEF_HASH_PREFIX);
    let canonical = serde_json::to_string(definition).unwrap_or_default();
    hasher.update(canonical.as_bytes());
    hex::encode(hasher.finalize())
}

/// Legacy name — hashes the first tool definition; ``policy_fingerprint`` is ignored.
#[must_use]
pub fn tools_content_hash(tools: &Value, _policy_fingerprint: &str) -> String {
    original_definition_for_entry(first_tool_value(tools))
        .map_or_else(String::new, |def| tool_definition_content_hash(&def))
}

fn first_tool_value(tools: &Value) -> &Value {
    tools
        .as_array()
        .and_then(|arr| arr.first())
        .unwrap_or(tools)
}

fn original_definition_for_entry(entry: &Value) -> Option<Value> {
    original_definition_for_entry_impl(entry)
}

pub fn original_definition_for_entry_impl(entry: &Value) -> Option<Value> {
    if let Some(full_schema) = entry.get("full_schema") {
        return Some(full_schema.clone());
    }
    if entry.get("name").is_some() {
        return Some(entry.clone());
    }
    None
}

fn tool_id_from_entry(entry: &Value) -> String {
    entry
        .get("id")
        .or_else(|| entry.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn enums_for_entry(entry: &Value) -> Vec<Value> {
    entry
        .pointer("/full_schema/inputSchema")
        .map(collect_enums)
        .unwrap_or_default()
}

fn build_index_for_entry(entry: &Value) -> CatalogIndex {
    let enums = enums_for_entry(entry);
    build_catalog_index(std::slice::from_ref(entry), &enums)
}

fn cache_files_only(index: &CatalogIndex) -> HashMap<String, String> {
    index
        .files
        .iter()
        .filter(|(rel, _)| !SKIP_CACHE_FILES.contains(&rel.as_str()))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

/// Write decomposed tool files synchronously (also used by the disk writer thread).
pub fn write_tool_cache_sync(entry_dir: &Path, index: &CatalogIndex) -> Result<(), String> {
    let files = cache_files_only(index);
    write_catalog_index(
        &CatalogIndex {
            tools: index.tools.clone(),
            files,
        },
        entry_dir,
        false,
    )
}

fn load_index_from_entry_dir(entry_dir: &Path, entry: &Value) -> Result<CatalogIndex, String> {
    let mut files = HashMap::new();
    collect_files(entry_dir, entry_dir, &mut files)?;
    Ok(CatalogIndex {
        tools: vec![entry.clone()],
        files,
    })
}

fn collect_files(
    root: &Path,
    current: &Path,
    files: &mut HashMap<String, String>,
) -> Result<(), String> {
    for entry in std::fs::read_dir(current).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if name.starts_with('.') {
            continue;
        }
        if path.is_dir() {
            collect_files(root, &path, files)?;
            continue;
        }
        if !path.is_file() {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        if SKIP_CACHE_FILES.contains(&rel.as_str()) {
            continue;
        }
        let content = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        files.insert(rel, content);
    }
    Ok(())
}

fn decomposed_root_path(tool_id: &str) -> String {
    format!(
        "{}/{tool_id}{}",
        decomposed_prefix().trim_end_matches('/'),
        json_ext()
    )
}

fn tool_cache_valid(entry_dir: &Path, tool_id: &str) -> bool {
    if !entry_dir.join("schemas").is_dir() {
        return false;
    }
    entry_dir.join(decomposed_root_path(tool_id)).is_file()
}

fn merge_catalog_indexes(parts: Vec<CatalogIndex>) -> CatalogIndex {
    let mut tools = Vec::new();
    let mut files = HashMap::new();
    for part in parts {
        tools.extend(part.tools);
        for (rel, content) in part.files {
            if SKIP_CACHE_FILES.contains(&rel.as_str()) {
                continue;
            }
            files.insert(rel, content);
        }
    }
    files.insert(
        "tools.json".into(),
        serde_json::to_string_pretty(&tools).unwrap_or_default(),
    );
    crate::token_enrichment::enrich_tool_schema_metadata(&mut files);
    CatalogIndex { tools, files }
}

struct SingleToolCacheOutcome {
    index: CatalogIndex,
    disk_backed: bool,
    cache_status: CacheStatus,
}

fn ensure_single_tool_cached(
    entry: &Value,
    tools_root: &Path,
    policy: CachePolicy,
) -> Result<SingleToolCacheOutcome, String> {
    let definition = original_definition_for_entry(entry)
        .ok_or_else(|| "tool entry missing original definition".to_string())?;
    let content_hash = tool_definition_content_hash(&definition);
    let tool_id = tool_id_from_entry(entry);
    let root = expand_tilde(tools_root);
    let entry_dir = root.join("entries").join(&content_hash);

    if let Some(cached) = get_tool_catalog(&content_hash) {
        return Ok(SingleToolCacheOutcome {
            index: cached,
            disk_backed: true,
            cache_status: CacheStatus::Hit,
        });
    }

    let try_disk = policy != CachePolicy::ForceMemory && disk_available(&entry_dir);
    if try_disk
        && tool_cache_valid(&entry_dir, &tool_id)
        && let Ok(index) = load_index_from_entry_dir(&entry_dir, entry)
    {
        store_tool_catalog(&content_hash, index.clone());
        return Ok(SingleToolCacheOutcome {
            index,
            disk_backed: true,
            cache_status: CacheStatus::Hit,
        });
    }

    if try_disk && let Ok(_lock) = BuildLock::acquire(&entry_dir) {
        if tool_cache_valid(&entry_dir, &tool_id)
            && let Ok(index) = load_index_from_entry_dir(&entry_dir, entry)
        {
            store_tool_catalog(&content_hash, index.clone());
            return Ok(SingleToolCacheOutcome {
                index,
                disk_backed: true,
                cache_status: CacheStatus::Hit,
            });
        }
        let index = build_index_for_entry(entry);
        store_tool_catalog(&content_hash, index.clone());
        maybe_enqueue_tool_catalog(entry_dir.clone(), index.clone());
        return Ok(SingleToolCacheOutcome {
            index,
            disk_backed: true,
            cache_status: CacheStatus::Miss,
        });
    }

    let index = build_index_for_entry(entry);
    store_tool_catalog(&content_hash, index.clone());
    Ok(SingleToolCacheOutcome {
        index,
        disk_backed: false,
        cache_status: CacheStatus::MemoryFallback,
    })
}

fn ensure_tool_catalog_from_entry_list(
    entries: &[Value],
    tools_root: &Path,
    policy: CachePolicy,
) -> Result<CacheResult<ToolCatalogHandle>, String> {
    let mut parts = Vec::with_capacity(entries.len());
    let mut any_disk = false;
    let mut any_miss = false;
    let mut any_fallback = false;

    for entry in entries {
        let outcome = ensure_single_tool_cached(entry, tools_root, policy)?;
        any_disk |= outcome.disk_backed;
        match outcome.cache_status {
            CacheStatus::Miss => any_miss = true,
            CacheStatus::MemoryFallback => any_fallback = true,
            CacheStatus::Hit => {}
        }
        parts.push(outcome.index);
    }

    let merged = merge_catalog_indexes(parts);
    let catalog = catalog_index_to_catalog_dict(&merged);
    let cache_status = if any_fallback {
        CacheStatus::MemoryFallback
    } else if any_miss {
        CacheStatus::Miss
    } else if entries.is_empty() {
        CacheStatus::MemoryFallback
    } else {
        CacheStatus::Hit
    };

    Ok(CacheResult {
        data: ToolCatalogHandle {
            catalog,
            index: merged,
            entry_dir: expand_tilde(tools_root),
            content_hash: String::new(),
            disk_backed: any_disk && !any_fallback,
            cache_status,
        },
        disk_backed: any_disk && !any_fallback,
        cache_status,
    })
}

/// Ensure decomposed catalog from Anthropic tool dicts.
///
/// # Errors
///
/// Returns an error when tool decomposition or cache writes fail.
pub fn ensure_tool_catalog(
    tools: &Value,
    _policy_fingerprint: &str,
    tools_root: &Path,
    policy: CachePolicy,
) -> Result<CacheResult<ToolCatalogHandle>, String> {
    let tools_arr = tools.as_array().cloned().unwrap_or_default();
    let (entries, _) = anthropic_tools_to_catalog_entries(&tools_arr);
    ensure_tool_catalog_from_entry_list(&entries, tools_root, policy)
}

/// Ensure decomposed catalog from prepared catalog entries and enums.
///
/// ``enums`` and ``policy_fingerprint`` are accepted for API compatibility; per-tool
/// enums are derived from each entry's ``full_schema`` and policy is not part of cache keys.
///
/// # Errors
///
/// Returns an error when tool decomposition or cache writes fail.
pub fn ensure_tool_catalog_from_entries(
    entries: &[Value],
    _enums: &[Value],
    _policy_fingerprint: &str,
    tools_root: &Path,
    policy: CachePolicy,
) -> Result<CacheResult<ToolCatalogHandle>, String> {
    ensure_tool_catalog_from_entry_list(entries, tools_root, policy)
}
