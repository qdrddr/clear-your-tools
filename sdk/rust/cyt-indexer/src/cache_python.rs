//! `PyO3` bindings for the Rust cache engine.

use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::cache::{
    CachePolicy, CacheStatus, configure_memory_cache, ensure_skills_registry_from_specs,
    ensure_tool_catalog, ensure_tool_catalog_from_entries, parse_skill_source_specs_json,
    parse_skill_sources, tools_content_hash,
};
use crate::pageindex::PageIndexConfig;

use super::{py_to_value, value_to_py};

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

fn page_index_config_from_py(config: Option<Bound<'_, PyAny>>) -> PyResult<PageIndexConfig> {
    match config {
        Some(obj) => Ok(PageIndexConfig::from_value(&py_to_value(obj)?)),
        None => Ok(PageIndexConfig::default()),
    }
}

#[pyfunction(name = "tools_catalog_content_hash")]
fn tools_catalog_content_hash_py(
    tools: Bound<'_, PyAny>,
    policy_fingerprint: &str,
) -> PyResult<String> {
    Ok(tools_content_hash(&py_to_value(tools)?, policy_fingerprint))
}

#[pyfunction(name = "ensure_tool_catalog")]
fn ensure_tool_catalog_py(
    py: Python<'_>,
    tools: Bound<'_, PyAny>,
    policy_fingerprint: &str,
    tools_root: &str,
    policy: Option<&str>,
) -> PyResult<Py<PyAny>> {
    let tools_val = py_to_value(tools)?;
    let result = py
        .detach(|| {
            ensure_tool_catalog(
                &tools_val,
                policy_fingerprint,
                PathBuf::from(tools_root).as_path(),
                cache_policy_from_str(policy),
            )
        })
        .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;
    let handle = &result.data;
    let dict = PyDict::new(py);
    dict.set_item("catalog", value_to_py(py, &handle.catalog)?)?;
    dict.set_item(
        "index",
        value_to_py(
            py,
            &serde_json::json!({"tools": handle.index.tools, "files": handle.index.files}),
        )?,
    )?;
    dict.set_item("entry_dir", handle.entry_dir.display().to_string())?;
    dict.set_item("content_hash", handle.content_hash.clone())?;
    dict.set_item("disk_backed", handle.disk_backed)?;
    dict.set_item("cache_status", cache_status_str(handle.cache_status))?;
    Ok(dict.into())
}

#[pyfunction(name = "ensure_skills_registry")]
fn ensure_skills_registry_py(
    py: Python<'_>,
    source_paths: Bound<'_, PyAny>,
    catalog_root: &str,
    pageindex_config: Option<Bound<'_, PyAny>>,
    pipeline: &str,
    index_params_hash: &str,
    policy: Option<&str>,
) -> PyResult<Py<PyAny>> {
    let sources_val = py_to_value(source_paths)?;
    let sources_arr = sources_val.as_array().cloned().unwrap_or_default();
    let specs = if sources_arr.iter().any(serde_json::Value::is_object) {
        parse_skill_source_specs_json(&sources_arr)
    } else {
        let paths: Vec<PathBuf> = sources_arr
            .iter()
            .filter_map(|v| v.as_str().map(PathBuf::from))
            .collect();
        Ok(parse_skill_sources(&paths))
    }
    .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;
    let cfg = page_index_config_from_py(pageindex_config)?;
    let refs = ensure_skills_registry_from_specs(
        &specs,
        PathBuf::from(catalog_root).as_path(),
        &cfg,
        pipeline,
        index_params_hash,
        cache_policy_from_str(policy),
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;

    let list = PyList::empty(py);
    for entry in refs {
        let dict = PyDict::new(py);
        dict.set_item("entry_dir", entry.entry_dir.display().to_string())?;
        dict.set_item("doc_id", entry.doc_id)?;
        dict.set_item("content_sha256", entry.content_sha256)?;
        dict.set_item(
            "bm25_chunk_dir",
            entry
                .bm25_chunk_dir
                .as_ref()
                .map(|p| p.display().to_string()),
        )?;
        dict.set_item("disk_backed", entry.disk_backed)?;
        dict.set_item("cache_status", cache_status_str(entry.cache_status))?;
        dict.set_item("source_path", entry.source_path)?;
        dict.set_item(
            "nodes_dir",
            entry.nodes_dir.as_ref().map(|p| p.display().to_string()),
        )?;
        if let Some(document) = &entry.document {
            dict.set_item("document", value_to_py(py, document)?)?;
        }
        dict.set_item("lazy_pending", entry.lazy_pending)?;
        list.append(dict)?;
    }
    Ok(list.into())
}

#[pyfunction(name = "ensure_tool_catalog_from_entries")]
fn ensure_tool_catalog_from_entries_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    enums: Bound<'_, PyAny>,
    policy_fingerprint: &str,
    tools_root: &str,
    policy: Option<&str>,
) -> PyResult<Py<PyAny>> {
    let entries_val = py_to_value(entries)?;
    let enums_val = py_to_value(enums)?;
    let entries_arr = entries_val.as_array().cloned().unwrap_or_default();
    let enums_arr = enums_val.as_array().cloned().unwrap_or_default();
    let result = ensure_tool_catalog_from_entries(
        &entries_arr,
        &enums_arr,
        policy_fingerprint,
        PathBuf::from(tools_root).as_path(),
        cache_policy_from_str(policy),
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;
    let handle = &result.data;
    let dict = PyDict::new(py);
    dict.set_item("catalog", value_to_py(py, &handle.catalog)?)?;
    dict.set_item(
        "index",
        value_to_py(
            py,
            &serde_json::json!({"tools": handle.index.tools, "files": handle.index.files}),
        )?,
    )?;
    dict.set_item("entry_dir", handle.entry_dir.display().to_string())?;
    dict.set_item("content_hash", handle.content_hash.clone())?;
    dict.set_item("disk_backed", handle.disk_backed)?;
    dict.set_item("cache_status", cache_status_str(handle.cache_status))?;
    Ok(dict.into())
}

#[pyfunction(name = "configure_memory_cache")]
fn configure_memory_cache_py(config: Bound<'_, PyAny>) -> PyResult<()> {
    configure_memory_cache(&py_to_value(config)?);
    Ok(())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(tools_catalog_content_hash_py, m)?)?;
    m.add_function(wrap_pyfunction!(ensure_tool_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(ensure_tool_catalog_from_entries_py, m)?)?;
    m.add_function(wrap_pyfunction!(ensure_skills_registry_py, m)?)?;
    m.add_function(wrap_pyfunction!(configure_memory_cache_py, m)?)?;
    Ok(())
}
