// N-API bindings for the Rust cache engine (included from `node.rs`).

use crate::cache::{
    ensure_skills_registry, ensure_tool_catalog, ensure_tool_catalog_from_entries,
    tools_content_hash, CachePolicy, CacheStatus,
};
use crate::pageindex::PageIndexConfig;
use std::path::PathBuf;

fn cache_policy_from_str(raw: Option<&str>) -> CachePolicy {
    match raw.map(str::trim) {
        Some(s) if s.eq_ignore_ascii_case("force_memory") || s.eq_ignore_ascii_case("memory") => {
            CachePolicy::ForceMemory
        }
        Some(s) if s.eq_ignore_ascii_case("force_disk") || s.eq_ignore_ascii_case("disk") => {
            CachePolicy::ForceDisk
        }
        _ => CachePolicy::Auto,
    }
}

const fn cache_status_str(status: CacheStatus) -> &'static str {
    match status {
        CacheStatus::Hit => "hit",
        CacheStatus::Miss => "miss",
        CacheStatus::MemoryFallback => "memory_fallback",
    }
}

fn page_index_config_from_value(config: Option<Value>) -> PageIndexConfig {
    config.map_or_else(PageIndexConfig::default, |val| PageIndexConfig::from_value(&val))
}

fn tool_catalog_handle_value(handle: &crate::cache::ToolCatalogHandle) -> Value {
    json!({
        "catalog": handle.catalog,
        "index": {"tools": handle.index.tools, "files": handle.index.files},
        "entry_dir": handle.entry_dir.display().to_string(),
        "content_hash": handle.content_hash,
        "disk_backed": handle.disk_backed,
        "cache_status": cache_status_str(handle.cache_status),
    })
}

/// # Errors
///
/// Returns an error when tool hashing fails.
#[napi(js_name = "toolsCatalogContentHash")]
pub fn tools_catalog_content_hash_napi(tools: Value, policy_fingerprint: String) -> Result<String> {
    let tools = Box::new(tools);
    let policy_fingerprint = policy_fingerprint.into_boxed_str();
    Ok(tools_content_hash(&tools, policy_fingerprint.as_ref()))
}

/// # Errors
///
/// Returns an error when tool catalog ensure fails.
#[napi(js_name = "ensureToolCatalog")]
pub fn ensure_tool_catalog_napi(
    tools: Value,
    policy_fingerprint: String,
    tools_root: String,
    policy: Option<String>,
) -> Result<Value> {
    let tools = Box::new(tools);
    let policy_fingerprint = policy_fingerprint.into_boxed_str();
    let tools_root = tools_root.into_boxed_str();
    let cache_policy = cache_policy_from_str(policy.as_deref());
    drop(policy);
    let result = ensure_tool_catalog(
        &tools,
        policy_fingerprint.as_ref(),
        PathBuf::from(tools_root.as_ref()).as_path(),
        cache_policy,
    )
    .map_err(Error::from_reason)?;
    Ok(tool_catalog_handle_value(&result.data))
}

/// # Errors
///
/// Returns an error when tool catalog ensure fails.
#[napi(js_name = "ensureToolCatalogFromEntries")]
pub fn ensure_tool_catalog_from_entries_napi(
    entries: Vec<Value>,
    enums: Vec<Value>,
    policy_fingerprint: String,
    tools_root: String,
    policy: Option<String>,
) -> Result<Value> {
    let entries = entries.into_boxed_slice();
    let enums = enums.into_boxed_slice();
    let policy_fingerprint = policy_fingerprint.into_boxed_str();
    let tools_root = tools_root.into_boxed_str();
    let cache_policy = cache_policy_from_str(policy.as_deref());
    drop(policy);
    let result = ensure_tool_catalog_from_entries(
        &entries,
        &enums,
        policy_fingerprint.as_ref(),
        PathBuf::from(tools_root.as_ref()).as_path(),
        cache_policy,
    )
    .map_err(Error::from_reason)?;
    Ok(tool_catalog_handle_value(&result.data))
}

/// # Errors
///
/// Returns an error when skills registry ensure fails.
#[napi(js_name = "ensureSkillsRegistry")]
pub fn ensure_skills_registry_napi(
    source_paths: Vec<String>,
    catalog_root: String,
    pageindex_config: Option<Value>,
    pipeline: String,
    index_params_hash: String,
    policy: Option<String>,
) -> Result<Vec<Value>> {
    let paths: Vec<PathBuf> = source_paths.into_iter().map(PathBuf::from).collect();
    let cfg = page_index_config_from_value(pageindex_config);
    let pipeline = pipeline.into_boxed_str();
    let index_params_hash = index_params_hash.into_boxed_str();
    let cache_policy = cache_policy_from_str(policy.as_deref());
    drop(policy);
    let refs = ensure_skills_registry(
        &paths,
        PathBuf::from(catalog_root).as_path(),
        &cfg,
        pipeline.as_ref(),
        index_params_hash.as_ref(),
        cache_policy,
    )
    .map_err(Error::from_reason)?;
    Ok(refs
        .into_iter()
        .map(|entry| {
            json!({
                "entry_dir": entry.entry_dir.display().to_string(),
                "doc_id": entry.doc_id,
                "content_sha256": entry.content_sha256,
                "bm25_chunk_dir": entry.bm25_chunk_dir.as_ref().map(|p| p.display().to_string()),
                "disk_backed": entry.disk_backed,
                "cache_status": cache_status_str(entry.cache_status),
                "source_path": entry.source_path,
                "nodes_dir": entry.nodes_dir.as_ref().map(|p| p.display().to_string()),
                "document": entry.document,
                "lazy_pending": entry.lazy_pending,
            })
        })
        .collect())
}

/// Apply in-memory cache tuning from a config object.
///
/// # Errors
///
/// Returns an error when the config object cannot be parsed.
#[napi(js_name = "configureMemoryCache")]
#[allow(clippy::needless_pass_by_value)]
pub fn configure_memory_cache_napi(config: Value) -> Result<()> {
    crate::cache::configure_memory_cache(&config);
    Ok(())
}
