#[path = "policies_python.rs"]
mod policies_python;

#[path = "pageindex_python.rs"]
mod pageindex_python;

#[path = "bm25_cohesion_python.rs"]
mod bm25_cohesion_python;

#[path = "bm25_search_python.rs"]
mod bm25_search_python;

#[path = "tokens_python.rs"]
mod tokens_python;

#[path = "cache_python.rs"]
mod cache_python;

#[path = "pipeline_python.rs"]
mod pipeline_python;

use crate::build::{build_catalog_index, catalog_index_from_value, catalog_tool_count};
use crate::paths::{self, PathConfig, collect_enums};
use crate::policies::policy_context_from_values;
use crate::retrieve::process_groups_options_from_fields;
use crate::retrieve::{
    DecomposedCatalog, ProcessGroupsOptions, RemovedChunksOptions, RetrieveOptions,
    build_process_groups_options, chunk_survivor_key, load_catalog_from_dir, removed_chunks,
    resolve_build_catalog, retrieve_core, retrieve_tools_from_catalog,
};
use crate::runtime_config::{self, RuntimeConfig};
use crate::tool_entries::{
    anthropic_tool_to_catalog_entry, build_catalog_from_tools, prepare_tool_entry,
    truncate_description,
};
use policies_python::ctx_from_py_any;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};
use pythonize::{depythonize, pythonize};
use serde_json::Value;
use std::path::PathBuf;

pub(crate) fn value_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    Ok(pythonize(py, value)?.into())
}

#[allow(clippy::needless_pass_by_value)]
pub(crate) fn py_to_value(obj: Bound<'_, PyAny>) -> PyResult<Value> {
    Ok(depythonize(&obj)?)
}

#[pyfunction(name = "catalog_tool_count")]
fn catalog_tool_count_py(data: Bound<'_, PyAny>) -> PyResult<usize> {
    Ok(catalog_tool_count(&py_to_value(data)?))
}

#[pyfunction(name = "build_catalog_index")]
fn build_catalog_index_py(
    tools: Bound<'_, PyAny>,
    all_enums: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let py = tools.py();
    let tools_val = py_to_value(tools)?;
    let enums_val = py_to_value(all_enums)?;
    let tools_arr = tools_val.as_array().cloned().unwrap_or_default();
    let enums_arr = enums_val.as_array().cloned().unwrap_or_default();
    let index = py.detach(|| build_catalog_index(&tools_arr, &enums_arr));

    let dict = PyDict::new(py);
    dict.set_item("tools", value_to_py(py, &Value::Array(index.tools))?)?;
    let files_dict = PyDict::new(py);
    for (k, v) in &index.files {
        files_dict.set_item(k, v)?;
    }
    dict.set_item("files", files_dict)?;
    Ok(dict.into())
}

fn optional_string_list(key: &str, policy: &Bound<'_, PyDict>) -> PyResult<Option<Vec<String>>> {
    match policy.get_item(key)? {
        None => Ok(None),
        Some(v) if v.is_none() => Ok(None),
        Some(v) => Ok(Some(v.extract()?)),
    }
}

fn string_list_or_empty(key: &str, policy: &Bound<'_, PyDict>) -> PyResult<Option<Vec<String>>> {
    Ok(Some(
        policy
            .get_item(key)?
            .map(|v| {
                if v.is_none() {
                    Ok(Vec::new())
                } else {
                    v.extract::<Vec<String>>()
                }
            })
            .transpose()?
            .unwrap_or_default(),
    ))
}

fn process_groups_from_policy_dict(
    policy: Option<Bound<'_, PyDict>>,
) -> PyResult<ProcessGroupsOptions> {
    let Some(policy) = policy else {
        return Ok(ProcessGroupsOptions::default());
    };
    let prune_optional_tools = string_list_or_empty("prune_optional_tools", &policy)?;
    let system_preserve = optional_string_list("system_preserve", &policy)?;
    let mcp_preserve = optional_string_list("mcp_preserve", &policy)?;
    let mut required_by_tool = None;
    let mut required_enum_values_by_tool = None;
    for key in ["required_by_tool", "required_enum_values_by_tool"] {
        if let Some(item) = policy.get_item(key)?
            && let Ok(dict) = item.cast_into::<PyDict>()
        {
            let map = dict_to_required_by_tool(&dict)?;
            let vec_map: std::collections::HashMap<String, Vec<String>> = map
                .into_iter()
                .map(|(k, v)| (k, v.into_iter().collect()))
                .collect();
            if key == "required_by_tool" {
                required_by_tool = Some(vec_map);
            } else {
                required_enum_values_by_tool = Some(vec_map);
            }
            break;
        }
    }
    Ok(process_groups_options_from_fields(
        system_preserve,
        mcp_preserve,
        required_by_tool,
        required_enum_values_by_tool,
        prune_optional_tools,
    ))
}

#[pyfunction(name = "retrieve_core")]
#[pyo3(signature = (data, store_json_files, survivor_json_files, apply_decomposed_score_filter=false, policy_options=None))]
fn retrieve_core_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    store_json_files: Bound<'_, PyAny>,
    survivor_json_files: Bound<'_, PyAny>,
    apply_decomposed_score_filter: bool,
    policy_options: Option<Bound<'_, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let data_val = py_to_value(data)?;
    let mut store = json_files_from_py(store_json_files)?;
    let survivor = json_files_from_py(survivor_json_files)?;

    let opts = RetrieveOptions {
        apply_decomposed_score_filter,
        process_groups: process_groups_from_policy_dict(policy_options)?,
    };

    let result = retrieve_core(&data_val, &mut store, &survivor, &opts);
    value_to_py(py, &Value::Array(result))
}

fn json_files_from_py(obj: Bound<'_, PyAny>) -> PyResult<DecomposedCatalog> {
    let val = py_to_value(obj)?;
    if let Some(map) = val.as_object() {
        return Ok(DecomposedCatalog::from_json_files(
            map.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        ));
    }
    Ok(DecomposedCatalog::default())
}

fn dict_to_required_by_tool(
    d: &Bound<'_, PyDict>,
) -> PyResult<std::collections::HashMap<String, std::collections::HashSet<String>>> {
    let mut map = std::collections::HashMap::new();
    for (k, v) in d.iter() {
        let key: String = k.extract()?;
        let items: Vec<String> = v.extract()?;
        map.insert(key, items.into_iter().collect());
    }
    Ok(map)
}

#[pyfunction(name = "to_decomposed_key")]
fn to_decomposed_key_py(file_path: &str) -> Option<String> {
    paths::to_decomposed_key(file_path)
}

#[pyfunction(name = "tool_id_from_decomposed_rel")]
fn tool_id_from_decomposed_rel_py(rel_path: &str) -> String {
    paths::tool_id_from_decomposed_rel(rel_path)
}

#[pyfunction(name = "get_root_tool_key")]
fn get_root_tool_key_py(file_path: &str) -> Option<String> {
    paths::get_root_tool_key(file_path)
}

#[pyfunction(name = "collect_enums")]
fn collect_enums_py(py: Python<'_>, schema: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let val = py_to_value(schema)?;
    let found = collect_enums(&val);
    value_to_py(py, &Value::Array(found))
}

#[pyfunction(name = "configure_path_constants")]
#[pyo3(signature = (md_ext, json_ext, decomposed_prefix, decomposed_root, catalog_prefix, default_catalog_dir, builder_flags))]
fn configure_path_constants_py(
    md_ext: &str,
    json_ext: &str,
    decomposed_prefix: &str,
    decomposed_root: &str,
    catalog_prefix: &str,
    default_catalog_dir: &str,
    builder_flags: (bool, bool),
) {
    let (builder_memory_only, write_catalog_prune) = builder_flags;
    let defaults = PathConfig::default();
    paths::configure(PathConfig {
        md_ext: md_ext.to_string(),
        json_ext: json_ext.to_string(),
        decomposed_prefix: decomposed_prefix.to_string(),
        decomposed_root: PathBuf::from(decomposed_root),
        skills_decomposed_prefix: defaults.skills_decomposed_prefix,
        skills_decomposed_root: defaults.skills_decomposed_root,
        catalog_prefix: catalog_prefix.to_string(),
        builder_memory_only,
        default_catalog_dir: PathBuf::from(default_catalog_dir),
        write_catalog_prune,
    });
}

#[pyfunction(name = "path_md_ext")]
fn path_md_ext_py() -> String {
    paths::md_ext()
}

#[pyfunction(name = "path_json_ext")]
fn path_json_ext_py() -> String {
    paths::json_ext()
}

#[pyfunction(name = "path_decomposed_prefix")]
fn path_decomposed_prefix_py() -> String {
    paths::decomposed_prefix()
}

#[pyfunction(name = "path_decomposed_root")]
fn path_decomposed_root_py() -> String {
    paths::decomposed_root().to_string_lossy().into_owned()
}

#[pyfunction(name = "path_catalog_prefix")]
fn path_catalog_prefix_py() -> String {
    paths::catalog_prefix()
}

#[pyfunction(name = "path_builder_memory_only")]
fn path_builder_memory_only_py() -> bool {
    paths::builder_memory_only()
}

#[pyfunction(name = "path_default_catalog_dir")]
fn path_default_catalog_dir_py() -> String {
    paths::default_catalog_dir().to_string_lossy().into_owned()
}

#[pyfunction(name = "path_write_catalog_prune")]
fn path_write_catalog_prune_py() -> bool {
    paths::write_catalog_prune()
}

#[pyfunction(name = "configure_runtime_defaults")]
#[pyo3(signature = (
    decomposed_score,
    enum_score,
    rerank_score,
    empty_optional_fallback_k,
    default_system_policy,
    default_mcp_policy,
))]
fn configure_runtime_defaults_py(
    py: Python<'_>,
    decomposed_score: f64,
    enum_score: f64,
    rerank_score: f64,
    empty_optional_fallback_k: usize,
    default_system_policy: &str,
    default_mcp_policy: &str,
) -> PyResult<()> {
    runtime_config::configure(RuntimeConfig {
        decomposed_score,
        enum_score,
        rerank_score,
        empty_optional_fallback_k,
        default_system_policy: default_system_policy.to_string(),
        default_mcp_policy: default_mcp_policy.to_string(),
    });
    if let Ok(m) = py.import("cyt_indexer._native") {
        policies_python::refresh_runtime_attrs(&m)?;
    }
    Ok(())
}

#[pyfunction(name = "runtime_decomposed_score")]
fn runtime_decomposed_score_py() -> f64 {
    runtime_config::decomposed_score()
}

#[pyfunction(name = "runtime_enum_score")]
fn runtime_enum_score_py() -> f64 {
    runtime_config::enum_score()
}

#[pyfunction(name = "runtime_rerank_score")]
fn runtime_rerank_score_py() -> f64 {
    runtime_config::rerank_score()
}

#[pyfunction(name = "runtime_empty_optional_fallback_k")]
fn runtime_empty_optional_fallback_k_py() -> usize {
    runtime_config::empty_optional_fallback_k()
}

#[pyfunction(name = "runtime_default_system_policy")]
fn runtime_default_system_policy_py() -> String {
    runtime_config::default_system_policy()
}

#[pyfunction(name = "runtime_default_mcp_policy")]
fn runtime_default_mcp_policy_py() -> String {
    runtime_config::default_mcp_policy()
}

#[pyfunction(name = "resolve_build_catalog")]
fn resolve_build_catalog_py(
    py: Python<'_>,
    catalog: &Bound<'_, PyAny>,
    survivor_data: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let survivor_val = py_to_value(survivor_data)?;
    let build = catalog_build_dict(catalog, &survivor_val)?;
    value_to_py(py, &build)
}

#[pyfunction(name = "retrieve_catalog_tool_count")]
fn retrieve_catalog_tool_count_py(data: Bound<'_, PyAny>) -> PyResult<i64> {
    let val = py_to_value(data)?;
    i64::try_from(crate::build::catalog_tool_count(&val)).map_err(|_| {
        PyErr::new::<pyo3::exceptions::PyOverflowError, _>("catalog tool count overflow")
    })
}

pub(crate) fn catalog_index_from_py(obj: Bound<'_, PyAny>) -> PyResult<crate::build::CatalogIndex> {
    if let Ok(native) = obj.extract::<PyRef<PyNativeCatalogIndex>>() {
        return Ok(native.inner.clone());
    }
    if obj.getattr("native_handle").is_ok()
        && let Ok(handle) = obj.call_method0("native_handle")
        && let Ok(native) = handle.extract::<PyRef<PyNativeCatalogIndex>>()
    {
        return Ok(native.inner.clone());
    }
    if obj.getattr("tools").is_ok() && obj.getattr("files").is_ok() {
        let val = serde_json::json!({
            "tools": py_to_value(obj.getattr("tools")?)?,
            "files": py_to_value(obj.getattr("files")?)?,
        });
        return Ok(catalog_index_from_value(&val));
    }
    Ok(catalog_index_from_value(&py_to_value(obj)?))
}

/// Native in-memory catalog index (avoids re-serializing large file maps across pipeline calls).
#[pyclass(name = "NativeCatalogIndex", from_py_object)]
#[derive(Clone)]
struct PyNativeCatalogIndex {
    inner: crate::build::CatalogIndex,
}

#[pymethods]
impl PyNativeCatalogIndex {
    #[classmethod]
    #[pyo3(signature = (tools, files))]
    fn from_parts(
        _cls: &Bound<'_, PyType>,
        tools: Bound<'_, PyAny>,
        files: Bound<'_, PyAny>,
    ) -> PyResult<Self> {
        let val = serde_json::json!({
            "tools": py_to_value(tools)?,
            "files": py_to_value(files)?,
        });
        Ok(Self {
            inner: catalog_index_from_value(&val),
        })
    }

    #[getter]
    fn tools(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, &Value::Array(self.inner.tools.clone()))
    }

    #[getter]
    fn files(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        for (k, v) in &self.inner.files {
            dict.set_item(k, v)?;
        }
        Ok(dict.into())
    }

    fn tool_schema_metadata(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, &self.inner.tool_schema_metadata())
    }
}

#[pyfunction(name = "catalog_index_tool_schema_metadata")]
fn catalog_index_tool_schema_metadata_py(
    py: Python<'_>,
    index: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let idx = catalog_index_from_py(index)?;
    value_to_py(py, &idx.tool_schema_metadata())
}

#[pyfunction(name = "catalog_index_to_catalog_dict")]
#[pyo3(signature = (index, catalog_prefix=None))]
fn catalog_index_to_catalog_dict_py(
    py: Python<'_>,
    index: Bound<'_, PyAny>,
    catalog_prefix: Option<&str>,
) -> PyResult<Py<PyAny>> {
    let idx = catalog_index_from_py(index)?;
    let val = catalog_prefix.map_or_else(
        || idx.to_catalog_dict(),
        |prefix| idx.to_catalog_dict_with_prefix(prefix),
    );
    value_to_py(py, &val)
}

#[pyfunction(name = "load_catalog")]
fn load_catalog_py(py: Python<'_>, dir_path: &str) -> PyResult<Py<PyAny>> {
    let catalog =
        load_catalog_from_dir(dir_path).map_err(PyErr::new::<pyo3::exceptions::PyOSError, _>)?;
    value_to_py(py, &catalog)
}

#[pyfunction(name = "chunk_survivor_key")]
fn chunk_survivor_key_py(item: Bound<'_, PyAny>, section: &str) -> PyResult<Option<String>> {
    Ok(chunk_survivor_key(&py_to_value(item)?, section))
}

#[pyfunction(name = "removed_chunks")]
#[pyo3(signature = (full_catalog, surviving, apply_decomposed_score_filter=false))]
fn removed_chunks_py(
    py: Python<'_>,
    full_catalog: Bound<'_, PyAny>,
    surviving: Bound<'_, PyAny>,
    apply_decomposed_score_filter: bool,
) -> PyResult<Py<PyAny>> {
    let removed = removed_chunks(
        &py_to_value(full_catalog)?,
        &py_to_value(surviving)?,
        &RemovedChunksOptions {
            apply_decomposed_score_filter,
        },
    );
    value_to_py(py, &removed)
}

/// In-memory decomposed catalog JSON (backed by Rust [`DecomposedCatalog`]).
#[pyclass(name = "DecomposedCatalog", from_py_object)]
#[derive(Clone)]
struct PyDecomposedCatalog {
    inner: DecomposedCatalog,
}

#[pymethods]
impl PyDecomposedCatalog {
    #[classmethod]
    fn from_catalog_index(_cls: &Bound<'_, PyType>, index: Bound<'_, PyAny>) -> PyResult<Self> {
        let idx = catalog_index_from_py(index)?;
        Ok(Self {
            inner: DecomposedCatalog::from_catalog_index(&idx),
        })
    }

    #[classmethod]
    fn from_catalog_dict(_cls: &Bound<'_, PyType>, data: Bound<'_, PyAny>) -> PyResult<Self> {
        let val = py_to_value(data)?;
        Ok(Self {
            inner: DecomposedCatalog::from_catalog_dict(&val),
        })
    }

    fn has_json(&self, key: &str) -> bool {
        self.inner.has_json(key)
    }

    fn get_json(&self, py: Python<'_>, key: &str) -> PyResult<Option<Py<PyAny>>> {
        self.inner
            .get_json(key)
            .map(|v| value_to_py(py, v))
            .transpose()
    }
}

fn catalog_to_decomposed(catalog: Bound<'_, PyAny>) -> PyResult<DecomposedCatalog> {
    if let Ok(py_cat) = catalog.extract::<PyRef<PyDecomposedCatalog>>() {
        return Ok(py_cat.inner.clone());
    }
    let idx = catalog_index_from_py(catalog)?;
    Ok(DecomposedCatalog::from_catalog_index(&idx))
}

#[pyfunction(name = "anthropic_tools_to_catalog_entries")]
fn anthropic_tools_to_catalog_entries_py(
    py: Python<'_>,
    tools: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let tools_val = py_to_value(tools)?;
    let tools_arr = tools_val.as_array().cloned().unwrap_or_default();
    let (entries, enums) = crate::tool_entries::anthropic_tools_to_catalog_entries(&tools_arr);
    let dict = PyDict::new(py);
    dict.set_item("entries", value_to_py(py, &Value::Array(entries))?)?;
    dict.set_item("enums", value_to_py(py, &Value::Array(enums))?)?;
    Ok(dict.into())
}

fn catalog_build_dict(catalog: &Bound<'_, PyAny>, survivor_data: &Value) -> PyResult<Value> {
    if let Ok(idx) = catalog_index_from_py(catalog.clone()) {
        return Ok(idx.to_catalog_dict());
    }
    let val = py_to_value(catalog.clone())?;
    Ok(resolve_build_catalog(&val, survivor_data))
}

#[pyfunction(name = "retrieve_tools")]
#[pyo3(signature = (data, catalog, apply_decomposed_score_filter=false, preserve_values=None, ctx=None))]
fn retrieve_tools_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    catalog: Bound<'_, PyAny>,
    apply_decomposed_score_filter: bool,
    preserve_values: Option<Vec<String>>,
    ctx: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let policy_ctx = match ctx {
        Some(c) => ctx_from_py_any(&c)?,
        None => policy_context_from_values(&Value::Object(serde_json::Map::new())),
    };
    let data_val = py_to_value(data)?;
    let build_catalog = catalog_build_dict(&catalog, &data_val)?;
    let mut store = catalog_to_decomposed(catalog)?;
    let preserve_set = preserve_values;
    let process_groups =
        build_process_groups_options(&policy_ctx, &build_catalog, &store, preserve_set);
    let opts = RetrieveOptions {
        apply_decomposed_score_filter,
        process_groups,
    };
    let result = py.detach(|| {
        retrieve_tools_from_catalog(&policy_ctx, &data_val, &build_catalog, &mut store, &opts)
    });
    value_to_py(py, &Value::Array(result))
}

#[pyfunction(name = "truncate_description")]
#[pyo3(signature = (description, max_tokens=60))]
fn truncate_description_py(description: &str, max_tokens: usize) -> String {
    truncate_description(description, max_tokens)
}

#[pyfunction(name = "prepare_tool_entry")]
fn prepare_tool_entry_py(
    py: Python<'_>,
    server_name: &str,
    name: &str,
    description: &str,
    input_schema: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let input_schema = py_to_value(input_schema)?;
    let entry = prepare_tool_entry(server_name, name, description, &input_schema);
    value_to_py(py, &entry)
}

#[pyfunction(name = "anthropic_tool_to_catalog_entry")]
fn anthropic_tool_to_catalog_entry_py(
    py: Python<'_>,
    tool: Bound<'_, PyAny>,
) -> PyResult<Option<Py<PyAny>>> {
    anthropic_tool_to_catalog_entry(&py_to_value(tool)?)
        .map(|entry| value_to_py(py, &entry))
        .transpose()
}

#[pyfunction(name = "build_catalog_from_tools")]
fn build_catalog_from_tools_py(py: Python<'_>, tools: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let tools_val = py_to_value(tools)?;
    let tools_arr = tools_val.as_array().cloned().unwrap_or_default();
    let index = build_catalog_from_tools(&tools_arr);
    let dict = PyDict::new(py);
    dict.set_item("tools", value_to_py(py, &Value::Array(index.tools))?)?;
    let files_dict = PyDict::new(py);
    for (k, v) in &index.files {
        files_dict.set_item(k, v)?;
    }
    dict.set_item("files", files_dict)?;
    Ok(dict.into())
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    policies_python::refresh_runtime_attrs(m)?;
    m.add_function(wrap_pyfunction!(configure_runtime_defaults_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_decomposed_score_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_enum_score_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_rerank_score_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_empty_optional_fallback_k_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_default_system_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_default_mcp_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_build_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(retrieve_catalog_tool_count_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_tool_count_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_catalog_index_py, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_tools_to_catalog_entries_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_catalog_from_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(prepare_tool_entry_py, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_tool_to_catalog_entry_py, m)?)?;
    m.add_function(wrap_pyfunction!(truncate_description_py, m)?)?;
    m.add_function(wrap_pyfunction!(retrieve_core_py, m)?)?;
    m.add_function(wrap_pyfunction!(load_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(chunk_survivor_key_py, m)?)?;
    m.add_function(wrap_pyfunction!(removed_chunks_py, m)?)?;
    m.add_function(wrap_pyfunction!(to_decomposed_key_py, m)?)?;
    m.add_function(wrap_pyfunction!(tool_id_from_decomposed_rel_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_root_tool_key_py, m)?)?;
    m.add_function(wrap_pyfunction!(collect_enums_py, m)?)?;
    m.add_function(wrap_pyfunction!(configure_path_constants_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_md_ext_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_json_ext_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_decomposed_prefix_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_decomposed_root_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_catalog_prefix_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_builder_memory_only_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_default_catalog_dir_py, m)?)?;
    m.add_function(wrap_pyfunction!(path_write_catalog_prune_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_index_to_catalog_dict_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_index_tool_schema_metadata_py, m)?)?;
    m.add_class::<PyDecomposedCatalog>()?;
    m.add_class::<PyNativeCatalogIndex>()?;
    m.add_function(wrap_pyfunction!(retrieve_tools_py, m)?)?;
    policies_python::register(m)?;
    pageindex_python::register(m)?;
    bm25_cohesion_python::register(m)?;
    bm25_search_python::register(m)?;
    tokens_python::register(m)?;
    cache_python::register(m)?;
    pipeline_python::register(m)?;
    Ok(())
}
