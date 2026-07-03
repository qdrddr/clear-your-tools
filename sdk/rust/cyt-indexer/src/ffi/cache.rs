//! Disk/memory cache FFI exports (mirrors `cache_python.rs`).

use std::path::PathBuf;

use crate::cache::{
    CachePolicy, CacheStatus, configure_memory_cache, ensure_skills_registry, ensure_tool_catalog,
    ensure_tool_catalog_from_entries, tools_content_hash,
};
use crate::ffi::error::CYT_ERR_NULL_PTR;
use crate::ffi::json_util::{
    c_str_to_str, json_array_or_empty, parse_json_cstr, run_ffi, write_json_out,
};
use crate::pageindex::PageIndexConfig;
use serde_json::{Value, json};
use std::os::raw::{c_char, c_int};

fn cache_policy_from_str(raw: Option<&str>) -> CachePolicy {
    match raw.map(str::trim).map(str::to_ascii_lowercase) {
        Some(s) if s == "force_memory" || s == "memory" => CachePolicy::ForceMemory,
        Some(s) if s == "force_disk" || s == "disk" => CachePolicy::ForceDisk,
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

fn tool_catalog_handle_json(handle: &crate::cache::ToolCatalogHandle) -> Value {
    json!({
        "catalog": handle.catalog,
        "index": {"tools": handle.index.tools, "files": handle.index.files},
        "entry_dir": handle.entry_dir.display().to_string(),
        "content_hash": handle.content_hash,
        "disk_backed": handle.disk_backed,
        "cache_status": cache_status_str(handle.cache_status),
    })
}

fn page_index_config_from_json(config_json: *const c_char) -> Result<PageIndexConfig, c_int> {
    if config_json.is_null() {
        return Ok(PageIndexConfig::default());
    }
    let val = unsafe { parse_json_cstr(config_json, "pageindex_config_json")? };
    Ok(PageIndexConfig::from_value(&val))
}

/// Hash tool catalog content for cache keying.
///
/// # Safety
///
/// `tools_json` and `policy_fingerprint` must be valid null-terminated UTF-8 C strings.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tools_catalog_content_hash(
    tools_json: *const c_char,
    policy_fingerprint: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let tools = parse_json_cstr(tools_json, "tools_json")?;
        let fp = c_str_to_str(policy_fingerprint, "policy_fingerprint")?;
        let hash = tools_content_hash(&tools, fp);
        unsafe { write_json_out(&Value::String(hash), out)? };
        Ok(())
    })
}

/// Ensure decomposed tool catalog from Anthropic tool dicts.
///
/// # Safety
///
/// All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_ensure_tool_catalog(
    tools_json: *const c_char,
    policy_fingerprint: *const c_char,
    tools_root: *const c_char,
    policy: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let tools = parse_json_cstr(tools_json, "tools_json")?;
        let fp = c_str_to_str(policy_fingerprint, "policy_fingerprint")?;
        let root = c_str_to_str(tools_root, "tools_root")?;
        let policy_str = if policy.is_null() {
            None
        } else {
            Some(c_str_to_str(policy, "policy")?)
        };
        let result = ensure_tool_catalog(
            &tools,
            fp,
            PathBuf::from(root).as_path(),
            cache_policy_from_str(policy_str),
        )
        .map_err(|e| {
            crate::ffi::error::set_error(&e);
            crate::ffi::error::CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&tool_catalog_handle_json(&result.data), out)? };
        Ok(())
    })
}

/// Ensure decomposed tool catalog from prepared entries and enums.
///
/// # Safety
///
/// All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_ensure_tool_catalog_from_entries(
    entries_json: *const c_char,
    enums_json: *const c_char,
    policy_fingerprint: *const c_char,
    tools_root: *const c_char,
    policy: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entries_val = parse_json_cstr(entries_json, "entries_json")?;
        let enums_val = parse_json_cstr(enums_json, "enums_json")?;
        let entries = json_array_or_empty(&entries_val);
        let enums = json_array_or_empty(&enums_val);
        let fp = c_str_to_str(policy_fingerprint, "policy_fingerprint")?;
        let root = c_str_to_str(tools_root, "tools_root")?;
        let policy_str = if policy.is_null() {
            None
        } else {
            Some(c_str_to_str(policy, "policy")?)
        };
        let result = ensure_tool_catalog_from_entries(
            &entries,
            &enums,
            fp,
            PathBuf::from(root).as_path(),
            cache_policy_from_str(policy_str),
        )
        .map_err(|e| {
            crate::ffi::error::set_error(&e);
            crate::ffi::error::CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&tool_catalog_handle_json(&result.data), out)? };
        Ok(())
    })
}

/// Ensure page index (+ BM25 chunks when pipeline is bm25) for skill sources.
///
/// # Safety
///
/// All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_ensure_skills_registry(
    source_paths_json: *const c_char,
    catalog_root: *const c_char,
    pageindex_config_json: *const c_char,
    pipeline: *const c_char,
    index_params_hash: *const c_char,
    policy: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let paths_val = parse_json_cstr(source_paths_json, "source_paths_json")?;
        let paths_arr = paths_val.as_array().cloned().unwrap_or_default();
        let paths: Vec<PathBuf> = paths_arr
            .iter()
            .filter_map(|v| v.as_str().map(PathBuf::from))
            .collect();
        let root = c_str_to_str(catalog_root, "catalog_root")?;
        let cfg = page_index_config_from_json(pageindex_config_json)?;
        let pipeline_s = c_str_to_str(pipeline, "pipeline")?;
        let hash = c_str_to_str(index_params_hash, "index_params_hash")?;
        let policy_str = if policy.is_null() {
            None
        } else {
            Some(c_str_to_str(policy, "policy")?)
        };
        let refs = ensure_skills_registry(
            &paths,
            PathBuf::from(root).as_path(),
            &cfg,
            pipeline_s,
            hash,
            cache_policy_from_str(policy_str),
        )
        .map_err(|e| {
            crate::ffi::error::set_error(&e);
            crate::ffi::error::CYT_ERR_INVALID_ARG
        })?;
        let list: Vec<Value> = refs
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
            .collect();
        unsafe { write_json_out(&Value::Array(list), out)? };
        Ok(())
    })
}

/// Apply in-memory cache tuning from a JSON object (`cache.memory` block).
///
/// # Safety
///
/// `config_json` must be a valid null-terminated UTF-8 C string when non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_configure_memory_cache(config_json: *const c_char) -> c_int {
    run_ffi(|| {
        let val = if config_json.is_null() {
            json!({})
        } else {
            parse_json_cstr(config_json, "config_json")?
        };
        configure_memory_cache(&val);
        Ok(())
    })
}
