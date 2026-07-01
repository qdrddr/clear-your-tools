//! `PyO3` bindings for policies (included from python.rs).

use crate::catalog_builder::CatalogBuilder as RustCatalogBuilder;
use crate::policies::{self, PolicyContext, parse_tool_policy, policy_context_from_values};
use crate::runtime_config;
use pyo3::Bound;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

/// Refresh module-level score constants after [`runtime_config::configure`].
pub fn refresh_runtime_attrs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("DECOMPOSED_SCORE", runtime_config::decomposed_score())?;
    m.add("ENUM_SCORE", runtime_config::enum_score())?;
    m.add("RERANK_SCORE", runtime_config::rerank_score())?;
    m.add(
        "EMPTY_OPTIONAL_FALLBACK_K",
        runtime_config::empty_optional_fallback_k(),
    )?;
    Ok(())
}

/// Python-facing policy context (defaults live in [`PolicyContext::new`]).
#[pyclass(name = "PolicyContext", from_py_object)]
#[derive(Clone)]
pub struct PyPolicyContext {
    pub inner: PolicyContext,
}

#[pymethods]
impl PyPolicyContext {
    #[new]
    #[pyo3(signature = (system_policy=None, mcp_policy=None))]
    fn new(system_policy: Option<String>, mcp_policy: Option<String>) -> Self {
        Self {
            inner: PolicyContext::with_overrides(
                system_policy.and_then(|s| parse_tool_policy(&s)),
                mcp_policy.and_then(|s| parse_tool_policy(&s)),
                HashMap::new(),
            ),
        }
    }

    #[getter]
    fn system_policy(&self) -> String {
        self.inner.system_policy.as_str().to_string()
    }

    #[setter]
    fn set_system_policy(&mut self, value: &str) {
        if let Some(p) = parse_tool_policy(value) {
            self.inner.system_policy = p;
        }
    }

    #[getter]
    fn mcp_policy(&self) -> String {
        self.inner.mcp_policy.as_str().to_string()
    }

    #[setter]
    fn set_mcp_policy(&mut self, value: &str) {
        if let Some(p) = parse_tool_policy(value) {
            self.inner.mcp_policy = p;
        }
    }

    #[getter]
    fn per_tool(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        for (k, v) in &self.inner.per_tool {
            dict.set_item(k, v.as_str())?;
        }
        Ok(dict.into())
    }

    #[setter]
    fn set_per_tool(&mut self, value: Bound<'_, PyAny>) -> PyResult<()> {
        if let Ok(dict) = value.cast_into::<PyDict>() {
            let mut per_tool = HashMap::new();
            for (k, v) in dict.iter() {
                let key: String = k.extract()?;
                let pol: String = v.extract()?;
                if let Some(p) = parse_tool_policy(&pol) {
                    per_tool.insert(key, p);
                }
            }
            self.inner.per_tool = per_tool;
        }
        Ok(())
    }
}

pub fn ctx_from_py_any(obj: &Bound<'_, PyAny>) -> PyResult<PolicyContext> {
    if let Ok(ctx) = obj.extract::<PyRef<PyPolicyContext>>() {
        return Ok(ctx.inner.clone());
    }
    let dict = obj.cast::<PyDict>()?;
    ctx_from_py(dict)
}

pub fn ctx_from_py(dict: &Bound<'_, PyDict>) -> PyResult<PolicyContext> {
    let system_policy = dict
        .get_item("system_policy")?
        .and_then(|v| v.extract::<String>().ok())
        .and_then(|s| parse_tool_policy(&s))
        .or_else(|| parse_tool_policy(&runtime_config::default_system_policy()));
    let mcp_policy = dict
        .get_item("mcp_policy")?
        .and_then(|v| v.extract::<String>().ok())
        .and_then(|s| parse_tool_policy(&s))
        .or_else(|| parse_tool_policy(&runtime_config::default_mcp_policy()));
    let mut per_tool = HashMap::new();
    if let Some(item) = dict.get_item("per_tool")?
        && let Ok(sub) = item.cast_into::<PyDict>()
    {
        for (k, v) in sub.iter() {
            let key: String = k.extract()?;
            let pol: String = v.extract()?;
            if let Some(p) = parse_tool_policy(&pol) {
                per_tool.insert(key, p);
            }
        }
    }
    Ok(PolicyContext::with_overrides(
        system_policy,
        mcp_policy,
        per_tool,
    ))
}

fn llm_selected_paths_from_py(
    paths: Option<Bound<'_, PyAny>>,
) -> PyResult<Option<HashSet<String>>> {
    let Some(obj) = paths else {
        return Ok(None);
    };
    if obj.is_none() {
        return Ok(None);
    }
    if let Ok(iter) = obj.try_iter() {
        let mut set = HashSet::new();
        for item in iter {
            set.insert(item?.extract::<String>()?);
        }
        return Ok(Some(set));
    }
    Ok(None)
}

#[pyfunction(name = "tool_policies")]
fn tool_policies_py(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let list: Vec<Value> = policies::tool_policy_strings()
        .into_iter()
        .map(|s| Value::String(s.to_string()))
        .collect();
    super::value_to_py(py, &Value::Array(list))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    refresh_runtime_attrs(m)?;
    m.add_class::<PyPolicyContext>()?;

    m.add_function(wrap_pyfunction!(tool_policies_py, m)?)?;
    m.add_function(wrap_pyfunction!(policy_context_from_values_py, m)?)?;
    m.add_function(wrap_pyfunction!(effective_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(tool_pass_through_py, m)?)?;
    m.add_function(wrap_pyfunction!(partition_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(merge_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_needs_partition_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_needs_pruned_recompose_py, m)?)?;
    m.add_function(wrap_pyfunction!(request_pass_through_py, m)?)?;
    m.add_function(wrap_pyfunction!(full_pass_through_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_decomposed_tool_root_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        is_decomposed_optional_property_chunk_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(filter_recompose_json_entries_py, m)?)?;
    m.add_function(wrap_pyfunction!(mitigate_empty_optional_properties_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        append_description_reinstate_entries_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(needs_description_reinstate_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_description_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(scoring_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        drop_recomposed_tools_with_empty_properties_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(extract_json_catalog_document_py, m)?)?;
    m.add_function(wrap_pyfunction!(extract_md_catalog_document_py, m)?)?;
    m.add_function(wrap_pyfunction!(write_catalog_index_py, m)?)?;
    m.add_class::<PyCatalogBuilder>()?;
    m.add_function(wrap_pyfunction!(root_tool_id_from_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_non_system_tool_id_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_system_tool_id_py, m)?)?;
    m.add_function(wrap_pyfunction!(chunk_tool_id_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_system_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_non_system_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_system_root_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_mcp_root_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_system_optional_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(is_mcp_optional_chunk_py, m)?)?;
    m.add_function(wrap_pyfunction!(stash_system_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(restore_system_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(stash_mcp_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(restore_mcp_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(merge_tools_preserving_order_py, m)?)?;
    m.add_function(wrap_pyfunction!(split_anthropic_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(entries_for_policy_py, m)?)?;
    m.add_function(wrap_pyfunction!(tools_for_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(system_required_enum_values_py, m)?)?;
    m.add_function(wrap_pyfunction!(mcp_required_enum_values_py, m)?)?;
    m.add_function(wrap_pyfunction!(required_enum_values_by_tool_py, m)?)?;
    m.add_function(wrap_pyfunction!(optional_leaf_survived_rerank_py, m)?)?;
    m.add_function(wrap_pyfunction!(needs_partition_py, m)?)?;
    m.add_function(wrap_pyfunction!(needs_pruned_recompose_py, m)?)?;
    m.add_function(wrap_pyfunction!(system_tools_pass_through_py, m)?)?;
    m.add_function(wrap_pyfunction!(mcp_tools_pass_through_py, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_tool_is_system_py, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_tool_is_mcp_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        direct_root_optional_chunks_for_tool_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(root_chunk_properties_empty_py, m)?)?;
    m.add_function(wrap_pyfunction!(tool_id_has_empty_decomposed_root_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        tool_id_had_empty_original_root_properties_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        is_direct_root_optional_property_chunk_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(extract_document_text_py, m)?)?;
    m.add_function(wrap_pyfunction!(extract_level_info_py, m)?)?;
    Ok(())
}

fn py_list_values(obj: Bound<'_, PyAny>) -> PyResult<Vec<Value>> {
    let val = super::py_to_value(obj)?;
    Ok(val.as_array().cloned().unwrap_or_default())
}

fn hashset_to_py_list(py: Python<'_>, set: HashSet<String>) -> PyResult<Py<PyAny>> {
    let list: Vec<Value> = set.into_iter().map(Value::String).collect();
    super::value_to_py(py, &Value::Array(list))
}

#[pyfunction(name = "policy_context_from_values")]
fn policy_context_from_values_py(config: Bound<'_, PyAny>) -> PyResult<PyPolicyContext> {
    let val = super::py_to_value(config)?;
    Ok(PyPolicyContext {
        inner: policy_context_from_values(&val),
    })
}

#[pyfunction(name = "effective_policy")]
fn effective_policy_py(ctx: &Bound<'_, PyAny>, tool_id: &str) -> PyResult<String> {
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::effective_policy(&ctx, tool_id)
        .as_str()
        .to_string())
}

#[pyfunction(name = "tool_pass_through")]
fn tool_pass_through_py(ctx: &Bound<'_, PyAny>, tool_id: &str) -> PyResult<bool> {
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::tool_pass_through(&ctx, tool_id))
}

#[pyfunction(name = "partition_catalog")]
fn partition_catalog_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let data_val = super::py_to_value(data)?;
    let ctx = ctx_from_py_any(ctx)?;
    let (proc, pinned) = policies::partition_catalog(&data_val, &ctx);
    Ok((
        super::value_to_py(py, &proc)?,
        super::value_to_py(py, &pinned)?,
    ))
}

#[pyfunction(name = "merge_catalog")]
fn merge_catalog_py(
    py: Python<'_>,
    processed: Bound<'_, PyAny>,
    pinned: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let proc = super::py_to_value(processed)?;
    let pin = super::py_to_value(pinned)?;
    super::value_to_py(py, &policies::merge_catalog(&proc, &pin))
}

#[pyfunction(name = "catalog_needs_partition")]
fn catalog_needs_partition_py(data: Bound<'_, PyAny>, ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    let data_val = super::py_to_value(data)?;
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::catalog_needs_partition(&data_val, &ctx))
}

#[pyfunction(name = "catalog_needs_pruned_recompose")]
fn catalog_needs_pruned_recompose_py(
    data: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let data_val = super::py_to_value(data)?;
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::catalog_needs_pruned_recompose(&data_val, &ctx))
}

#[pyfunction(name = "request_pass_through")]
fn request_pass_through_py(ctx: &Bound<'_, PyAny>, tools: Bound<'_, PyAny>) -> PyResult<bool> {
    let ctx = ctx_from_py_any(ctx)?;
    let val = super::py_to_value(tools)?;
    let arr = val.as_array().cloned().unwrap_or_default();
    Ok(policies::request_pass_through(&ctx, &arr))
}

#[pyfunction(name = "full_pass_through")]
fn full_pass_through_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::full_pass_through(&ctx))
}

#[pyfunction(name = "is_decomposed_tool_root_chunk")]
fn is_decomposed_tool_root_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_decomposed_tool_root_chunk(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "is_decomposed_optional_property_chunk")]
fn is_decomposed_optional_property_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_decomposed_optional_property_chunk(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "filter_recompose_json_entries")]
#[pyo3(signature = (json_list, ctx, rerank_score=None, llm_selected_paths=None))]
fn filter_recompose_json_entries_py(
    py: Python<'_>,
    json_list: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let val = super::py_to_value(json_list)?;
    let arr = val.as_array().cloned().unwrap_or_default();
    let paths_set = llm_selected_paths_from_py(llm_selected_paths)?;
    let filtered = policies::filter_recompose_json_entries(
        &ctx,
        &arr,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        paths_set.as_ref(),
    );
    super::value_to_py(py, &Value::Array(filtered))
}

#[pyfunction(name = "mitigate_empty_optional_properties")]
fn mitigate_empty_optional_properties_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
    post_rerank_scored: Option<Bound<'_, PyAny>>,
    pipeline: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let entries_val = super::py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let index = super::catalog_index_from_py(catalog_index)?;
    let scored = post_rerank_scored.map(super::py_to_value).transpose()?;
    let pipeline_vec = pipeline.extract::<Vec<String>>()?;
    let result = policies::mitigate_empty_optional_properties(
        &ctx,
        &arr,
        &index,
        scored.as_ref(),
        &pipeline_vec,
    );
    super::value_to_py(py, &Value::Array(result))
}

#[pyfunction(name = "append_description_reinstate_entries")]
fn append_description_reinstate_entries_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    build_catalog: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let entries_val = super::py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let build_val = super::py_to_value(build_catalog)?;
    let index = super::catalog_index_from_py(catalog_index)?;
    let result = policies::append_description_reinstate_entries(&ctx, &arr, &build_val, &index);
    super::value_to_py(py, &Value::Array(result))
}

#[pyfunction(name = "needs_description_reinstate")]
fn needs_description_reinstate_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    let ctx = ctx_from_py_any(ctx)?;
    Ok(policies::needs_description_reinstate(&ctx))
}

#[pyfunction(name = "is_description_policy")]
fn is_description_policy_py(policy: &str) -> bool {
    let Some(p) = parse_tool_policy(policy) else {
        return false;
    };
    policies::is_description_policy(p)
}

#[pyfunction(name = "scoring_policy")]
fn scoring_policy_py(policy: &str) -> PyResult<String> {
    let p = parse_tool_policy(policy).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid policy: {policy}"))
    })?;
    Ok(policies::scoring_policy(p).as_str().to_string())
}

#[pyfunction(name = "drop_recomposed_tools_with_empty_properties")]
fn drop_recomposed_tools_with_empty_properties_py(
    py: Python<'_>,
    tools: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let val = super::py_to_value(tools)?;
    let arr = val.as_array().cloned().unwrap_or_default();
    let index = super::catalog_index_from_py(catalog_index)?;
    let result = policies::drop_recomposed_tools_with_empty_properties(&ctx, &arr, &index);
    super::value_to_py(py, &Value::Array(result))
}

#[pyfunction(name = "extract_json_catalog_document")]
fn extract_json_catalog_document_py(item: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    Ok(crate::documents::extract_json_catalog_document(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "extract_md_catalog_document")]
fn extract_md_catalog_document_py(item: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    Ok(crate::documents::extract_md_catalog_document(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "write_catalog_index")]
#[pyo3(signature = (index, output_dir=None, prune=None))]
fn write_catalog_index_py(
    index: Bound<'_, PyAny>,
    output_dir: Option<&str>,
    prune: Option<bool>,
) -> PyResult<()> {
    let catalog = super::catalog_index_from_py(index)?;
    crate::catalog_io::write_catalog_index_resolved(
        &catalog,
        output_dir.map(std::path::Path::new),
        prune,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyOSError, _>)
}

#[pyfunction(name = "root_tool_id_from_chunk")]
fn root_tool_id_from_chunk_py(item: Bound<'_, PyAny>) -> PyResult<String> {
    Ok(policies::root_tool_id_from_chunk(&super::py_to_value(
        item,
    )?))
}

#[pyfunction(name = "is_non_system_tool_id")]
fn is_non_system_tool_id_py(tool_id: &str) -> bool {
    policies::is_non_system_tool_id(tool_id)
}

#[pyfunction(name = "is_system_tool_id")]
fn is_system_tool_id_py(tool_id: &str) -> bool {
    policies::is_system_tool_id(tool_id)
}

#[pyfunction(name = "chunk_tool_id")]
fn chunk_tool_id_py(item: Bound<'_, PyAny>) -> PyResult<String> {
    Ok(policies::chunk_tool_id(&super::py_to_value(item)?))
}

#[pyfunction(name = "is_system_chunk")]
fn is_system_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_system_chunk(&super::py_to_value(item)?))
}

#[pyfunction(name = "is_non_system_chunk")]
fn is_non_system_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_non_system_chunk(&super::py_to_value(item)?))
}

#[pyfunction(name = "is_system_root_chunk")]
fn is_system_root_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_system_root_chunk(&super::py_to_value(item)?))
}

#[pyfunction(name = "is_mcp_root_chunk")]
fn is_mcp_root_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_mcp_root_chunk(&super::py_to_value(item)?))
}

#[pyfunction(name = "is_system_optional_chunk")]
fn is_system_optional_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_system_optional_chunk(&super::py_to_value(
        item,
    )?))
}

#[pyfunction(name = "is_mcp_optional_chunk")]
fn is_mcp_optional_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_mcp_optional_chunk(&super::py_to_value(item)?))
}

#[pyfunction(name = "stash_system_tools")]
fn stash_system_tools_py(py: Python<'_>, tools: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let arr = py_list_values(tools)?;
    super::value_to_py(py, &Value::Array(policies::stash_system_tools(&arr)))
}

#[pyfunction(name = "restore_system_tools")]
fn restore_system_tools_py(py: Python<'_>, stash: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let arr = py_list_values(stash)?;
    super::value_to_py(py, &Value::Array(policies::restore_system_tools(&arr)))
}

#[pyfunction(name = "stash_mcp_tools")]
fn stash_mcp_tools_py(py: Python<'_>, tools: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let arr = py_list_values(tools)?;
    super::value_to_py(py, &Value::Array(policies::stash_mcp_tools(&arr)))
}

#[pyfunction(name = "restore_mcp_tools")]
fn restore_mcp_tools_py(py: Python<'_>, stash: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let arr = py_list_values(stash)?;
    super::value_to_py(py, &Value::Array(policies::restore_mcp_tools(&arr)))
}

#[pyfunction(name = "merge_tools_preserving_order")]
fn merge_tools_preserving_order_py(
    py: Python<'_>,
    original: Bound<'_, PyAny>,
    pruned_by_name: &Bound<'_, PyDict>,
    stashed_by_name: &Bound<'_, PyDict>,
) -> PyResult<Py<PyAny>> {
    let orig = py_list_values(original)?;
    let mut pruned = HashMap::new();
    for (k, v) in pruned_by_name.iter() {
        pruned.insert(k.extract::<String>()?, super::py_to_value(v)?);
    }
    let mut stashed = HashMap::new();
    for (k, v) in stashed_by_name.iter() {
        stashed.insert(k.extract::<String>()?, super::py_to_value(v)?);
    }
    let result = policies::merge_tools_preserving_order(&orig, &pruned, &stashed);
    super::value_to_py(py, &Value::Array(result))
}

#[pyfunction(name = "split_anthropic_tools")]
fn split_anthropic_tools_py(
    py: Python<'_>,
    tools: Bound<'_, PyAny>,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let arr = py_list_values(tools)?;
    let (non_system, system) = policies::split_anthropic_tools(&arr);
    Ok((
        super::value_to_py(py, &Value::Array(non_system))?,
        super::value_to_py(py, &Value::Array(system))?,
    ))
}

#[pyfunction(name = "entries_for_policy")]
fn entries_for_policy_py(
    py: Python<'_>,
    ctx: &Bound<'_, PyAny>,
    all_entries: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let arr = py_list_values(all_entries)?;
    super::value_to_py(py, &Value::Array(policies::entries_for_policy(&ctx, &arr)))
}

#[pyfunction(name = "tools_for_catalog")]
fn tools_for_catalog_py(
    py: Python<'_>,
    ctx: &Bound<'_, PyAny>,
    tools: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let ctx = ctx_from_py_any(ctx)?;
    let arr = py_list_values(tools)?;
    super::value_to_py(py, &Value::Array(policies::tools_for_catalog(&ctx, &arr)))
}

#[pyfunction(name = "system_required_enum_values")]
fn system_required_enum_values_py(py: Python<'_>, data: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let val = super::py_to_value(data)?;
    hashset_to_py_list(py, policies::system_required_enum_values(&val))
}

#[pyfunction(name = "mcp_required_enum_values")]
fn mcp_required_enum_values_py(py: Python<'_>, data: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let val = super::py_to_value(data)?;
    hashset_to_py_list(py, policies::mcp_required_enum_values(&val))
}

#[pyfunction(name = "required_enum_values_by_tool")]
fn required_enum_values_by_tool_py<'py>(
    py: Python<'py>,
    data: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let val = super::py_to_value(data)?;
    let map = policies::required_enum_values_by_tool(&val);
    let dict = PyDict::new(py);
    for (tool_id, vals) in map {
        let list: Vec<Value> = vals.into_iter().map(Value::String).collect();
        dict.set_item(tool_id, super::value_to_py(py, &Value::Array(list))?)?;
    }
    Ok(dict)
}

#[pyfunction(name = "optional_leaf_survived_rerank")]
#[pyo3(signature = (item, ctx, rerank_score=None, llm_selected_paths=None))]
fn optional_leaf_survived_rerank_py(
    item: Bound<'_, PyAny>,
    ctx: &Bound<'_, PyAny>,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Bound<'_, PyAny>>,
) -> PyResult<bool> {
    let ctx = ctx_from_py_any(ctx)?;
    let item_val = super::py_to_value(item)?;
    let paths_set = llm_selected_paths_from_py(llm_selected_paths)?;
    Ok(policies::optional_leaf_survived_rerank(
        &ctx,
        &item_val,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        paths_set.as_ref(),
    ))
}

#[pyfunction(name = "needs_partition")]
fn needs_partition_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::needs_partition(&ctx_from_py_any(ctx)?))
}

#[pyfunction(name = "needs_pruned_recompose")]
fn needs_pruned_recompose_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::needs_pruned_recompose(&ctx_from_py_any(ctx)?))
}

#[pyfunction(name = "system_tools_pass_through")]
fn system_tools_pass_through_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::system_tools_pass_through(&ctx_from_py_any(ctx)?))
}

#[pyfunction(name = "mcp_tools_pass_through")]
fn mcp_tools_pass_through_py(ctx: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::mcp_tools_pass_through(&ctx_from_py_any(ctx)?))
}

#[pyfunction(name = "anthropic_tool_is_system")]
fn anthropic_tool_is_system_py(tool: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::anthropic_tool_is_system(&super::py_to_value(
        tool,
    )?))
}

#[pyfunction(name = "anthropic_tool_is_mcp")]
fn anthropic_tool_is_mcp_py(tool: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::anthropic_tool_is_mcp(&super::py_to_value(tool)?))
}

#[pyfunction(name = "direct_root_optional_chunks_for_tool")]
fn direct_root_optional_chunks_for_tool_py(
    py: Python<'_>,
    items: Bound<'_, PyAny>,
    tool_id: &str,
) -> PyResult<Py<PyAny>> {
    let arr = py_list_values(items)?;
    let out = policies::direct_root_optional_chunks_for_tool(&arr, tool_id);
    super::value_to_py(py, &Value::Array(out))
}

#[pyfunction(name = "root_chunk_properties_empty")]
fn root_chunk_properties_empty_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::root_chunk_properties_empty(&super::py_to_value(
        item,
    )?))
}

#[pyfunction(name = "tool_id_has_empty_decomposed_root")]
fn tool_id_has_empty_decomposed_root_py(
    catalog_index: Bound<'_, PyAny>,
    tool_id: &str,
) -> PyResult<bool> {
    let index = super::catalog_index_from_py(catalog_index)?;
    Ok(policies::tool_id_has_empty_decomposed_root(&index, tool_id))
}

#[pyfunction(name = "tool_id_had_empty_original_root_properties")]
fn tool_id_had_empty_original_root_properties_py(
    catalog_index: Bound<'_, PyAny>,
    tool_id: &str,
) -> PyResult<bool> {
    let index = super::catalog_index_from_py(catalog_index)?;
    Ok(policies::tool_id_had_empty_original_root_properties(
        &index, tool_id,
    ))
}

#[pyfunction(name = "is_direct_root_optional_property_chunk")]
fn is_direct_root_optional_property_chunk_py(item: Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(policies::is_direct_root_optional_property_chunk(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "extract_document_text")]
fn extract_document_text_py(item: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    Ok(crate::documents::extract_document_text(
        &super::py_to_value(item)?,
    ))
}

#[pyfunction(name = "extract_level_info")]
fn extract_level_info_py(py: Python<'_>, item: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let lines = crate::documents::extract_level_info(&super::py_to_value(item)?);
    let list: Vec<Value> = lines.into_iter().map(Value::String).collect();
    super::value_to_py(py, &Value::Array(list))
}

#[pyclass(name = "CatalogBuilder")]
struct PyCatalogBuilder {
    inner: RustCatalogBuilder,
}

#[pymethods]
impl PyCatalogBuilder {
    #[new]
    #[pyo3(signature = (memory_only=None, output_dir=None))]
    fn new(memory_only: Option<bool>, output_dir: Option<String>) -> Self {
        let dir = output_dir.map(std::path::PathBuf::from);
        Self {
            inner: RustCatalogBuilder::new_with_options(memory_only, dir),
        }
    }

    fn add_tool(&mut self, entry: Bound<'_, PyAny>) -> PyResult<()> {
        self.inner.add_tool(super::py_to_value(entry)?);
        Ok(())
    }

    fn get_tool_info(
        &self,
        py: Python<'_>,
        server_name: &str,
        tool_name: &str,
    ) -> PyResult<Option<Py<PyAny>>> {
        self.inner
            .get_tool_info(server_name, tool_name)
            .map(|v| super::value_to_py(py, v))
            .transpose()
    }

    fn build_index(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let index = self.inner.build_index();
        super::value_to_py(
            py,
            &serde_json::json!({
                "tools": index.tools,
                "files": index.files,
            }),
        )
    }

    fn write_catalog(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let index = self
            .inner
            .write_catalog()
            .map_err(PyErr::new::<pyo3::exceptions::PyOSError, _>)?;
        super::value_to_py(
            py,
            &serde_json::json!({
                "tools": index.tools,
                "files": index.files,
            }),
        )
    }

    #[pyo3(name = "to_catalog_dict", signature = (catalog_prefix=None))]
    fn build_catalog_dict(
        &mut self,
        py: Python<'_>,
        catalog_prefix: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let val = match catalog_prefix {
            Some(prefix) => self.inner.to_catalog_dict_with_prefix(prefix),
            None => self.inner.to_catalog_dict(),
        };
        super::value_to_py(py, &val)
    }
}
