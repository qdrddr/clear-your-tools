//! `PyO3` bindings for composite pipeline APIs (included from python.rs).

use super::policies_python::ctx_from_py_any;
use crate::pipeline::{
    CoordinateBm25Options, PruneBm25Options, SearchSkillsOptions, build_skill_node_catalog,
    classify_and_count_catalog, coordinate_bm25_prune, prune_catalog_bm25_and_retrieve,
    recompose_and_retrieve_tools, search_skills_and_select,
};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::{Map, Value, json};

use super::{py_to_value, value_to_py};

fn options_from_py(dict: Option<&Bound<'_, PyDict>>) -> PyResult<PruneBm25Options> {
    let mut opts = PruneBm25Options::default();
    let Some(dict) = dict else {
        return Ok(opts);
    };
    if let Some(v) = dict
        .get_item("score_tool")?
        .and_then(|x| x.extract::<f64>().ok())
    {
        opts.score_tool = v;
    }
    if let Some(v) = dict
        .get_item("score_tool_enum")?
        .and_then(|x| x.extract::<f64>().ok())
    {
        opts.score_tool_enum = v;
    }
    if let Some(v) = dict
        .get_item("prune_enums")?
        .and_then(|x| x.extract::<bool>().ok())
    {
        opts.prune_enums = v;
    }
    if let Some(item) = dict.get_item("pipeline")?
        && let Ok(iter) = item.try_iter()
    {
        opts.pipeline = iter
            .filter_map(|x| x.ok()?.extract::<String>().ok())
            .collect();
    }
    Ok(opts)
}

fn search_options_from_py(dict: Option<&Bound<'_, PyDict>>) -> PyResult<SearchSkillsOptions> {
    let mut opts = SearchSkillsOptions::default();
    let Some(dict) = dict else {
        return Ok(opts);
    };
    if let Some(v) = dict
        .get_item("threshold")?
        .and_then(|x| x.extract::<f64>().ok())
    {
        opts.threshold = v;
    }
    if let Some(v) = dict
        .get_item("max_tokens")?
        .and_then(|x| x.extract::<i64>().ok())
    {
        opts.max_tokens = v;
    }
    if let Some(v) = dict
        .get_item("frontmatter_upper_limit")?
        .and_then(|x| x.extract::<f64>().ok())
    {
        opts.frontmatter_upper_limit = Some(v);
    }
    if let Some(v) = dict
        .get_item("item_kind")?
        .and_then(|x| x.extract::<String>().ok())
    {
        opts.item_kind = v;
    }
    Ok(opts)
}

fn prune_result_to_py(
    py: Python<'_>,
    result: crate::pipeline::PruneRetrieveResult,
) -> PyResult<Py<PyAny>> {
    let decomposed: Map<String, Value> = result
        .decomposed
        .into_iter()
        .map(|(k, v)| (k, Value::from(v)))
        .collect();
    let breakdown: Map<String, Value> = result
        .decomposed_breakdown
        .into_iter()
        .map(|(stage, counts)| {
            let inner: Map<String, Value> = counts
                .into_iter()
                .map(|(k, v)| (k, Value::from(v)))
                .collect();
            (stage, Value::Object(inner))
        })
        .collect();
    value_to_py(
        py,
        &json!({
            "tools": result.tools,
            "decomposed": decomposed,
            "decomposed_breakdown": breakdown,
            "optional_chunk_count_in": result.optional_chunk_count_in,
            "optional_chunk_count_out": result.optional_chunk_count_out,
        }),
    )
}

#[pyfunction(name = "prune_catalog_bm25_and_retrieve")]
#[pyo3(signature = (catalog_data, build_catalog, catalog_index, query, scoring_ctx, output_ctx, options=None))]
#[allow(clippy::too_many_arguments)]
fn prune_catalog_bm25_and_retrieve_py(
    py: Python<'_>,
    catalog_data: Bound<'_, PyAny>,
    build_catalog: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    query: &str,
    scoring_ctx: &Bound<'_, PyAny>,
    output_ctx: &Bound<'_, PyAny>,
    options: Option<&Bound<'_, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let catalog_val = py_to_value(catalog_data)?;
    let build_val = py_to_value(build_catalog)?;
    let index = super::catalog_index_from_py(catalog_index)?;
    let scoring = ctx_from_py_any(scoring_ctx)?;
    let output = ctx_from_py_any(output_ctx)?;
    let opts = options_from_py(options)?;
    let result = prune_catalog_bm25_and_retrieve(
        &catalog_val,
        &build_val,
        &index,
        query,
        &scoring,
        &output,
        &opts,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    prune_result_to_py(py, result)
}

#[pyfunction(name = "recompose_and_retrieve_tools")]
#[pyo3(signature = (data, build_catalog, catalog_index, post_rerank, post_rerank_scored, pinned, pipeline, scoring_ctx, output_ctx))]
#[allow(clippy::too_many_arguments)]
fn recompose_and_retrieve_tools_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    build_catalog: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    post_rerank: Option<Bound<'_, PyAny>>,
    post_rerank_scored: Option<Bound<'_, PyAny>>,
    pinned: Option<Bound<'_, PyAny>>,
    pipeline: Vec<String>,
    scoring_ctx: &Bound<'_, PyAny>,
    output_ctx: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let data_val = py_to_value(data)?;
    let build_val = py_to_value(build_catalog)?;
    let index = super::catalog_index_from_py(catalog_index)?;
    let post_rerank_val = post_rerank.map(py_to_value).transpose()?;
    let post_rerank_scored_val = post_rerank_scored.map(py_to_value).transpose()?;
    let pinned_val = pinned.map(py_to_value).transpose()?;
    let scoring = ctx_from_py_any(scoring_ctx)?;
    let output = ctx_from_py_any(output_ctx)?;
    let tools = recompose_and_retrieve_tools(
        &data_val,
        &build_val,
        &index,
        post_rerank_val.as_ref(),
        post_rerank_scored_val.as_ref(),
        pinned_val.as_ref(),
        &pipeline,
        &scoring,
        &output,
    );
    value_to_py(py, &Value::Array(tools))
}

#[pyfunction(name = "classify_and_count_catalog")]
#[pyo3(signature = (catalog_data, tools=None))]
fn classify_and_count_catalog_py(
    py: Python<'_>,
    catalog_data: Bound<'_, PyAny>,
    tools: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let catalog_val = py_to_value(catalog_data)?;
    let tools_val = tools.map(py_to_value).transpose()?;
    let tools_slice = tools_val
        .as_ref()
        .and_then(Value::as_array)
        .map(std::vec::Vec::as_slice);
    let result = classify_and_count_catalog(&catalog_val, tools_slice)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &result)
}

#[pyfunction(name = "search_skills_and_select")]
#[pyo3(signature = (entries, query, options=None))]
fn search_skills_and_select_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    query: &str,
    options: Option<&Bound<'_, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let entries_val = py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let opts = search_options_from_py(options)?;
    let result = search_skills_and_select(&arr, query, &opts)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &result)
}

#[pyfunction(name = "build_skill_node_catalog")]
fn build_skill_node_catalog_py(py: Python<'_>, entries: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let entries_val = py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let items =
        build_skill_node_catalog(&arr).map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &Value::Array(items))
}

fn coordinate_options_from_py(dict: Option<&Bound<'_, PyDict>>) -> PyResult<CoordinateBm25Options> {
    let mut opts = CoordinateBm25Options::default();
    let Some(dict) = dict else {
        return Ok(opts);
    };
    if let Ok(Some(item)) = dict.get_item("skills")
        && let Ok(sub) = item.cast::<PyDict>()
    {
        opts.skills = search_options_from_py(Some(sub))?;
    }
    if let Ok(Some(item)) = dict.get_item("tools")
        && let Ok(sub) = item.cast::<PyDict>()
    {
        opts.tools = options_from_py(Some(sub))?;
    }
    Ok(opts)
}

#[pyfunction(name = "coordinate_bm25_prune")]
#[pyo3(signature = (skills_entries, catalog_data, build_catalog, catalog_index, query, scoring_ctx, output_ctx, options=None))]
#[allow(clippy::too_many_arguments)]
fn coordinate_bm25_prune_py(
    py: Python<'_>,
    skills_entries: Bound<'_, PyAny>,
    catalog_data: Bound<'_, PyAny>,
    build_catalog: Bound<'_, PyAny>,
    catalog_index: Bound<'_, PyAny>,
    query: &str,
    scoring_ctx: &Bound<'_, PyAny>,
    output_ctx: &Bound<'_, PyAny>,
    options: Option<&Bound<'_, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let skills_val = py_to_value(skills_entries)?;
    let skills_arr = skills_val.as_array().cloned().unwrap_or_default();
    let catalog_val = py_to_value(catalog_data)?;
    let build_val = py_to_value(build_catalog)?;
    let index = super::catalog_index_from_py(catalog_index)?;
    let scoring = ctx_from_py_any(scoring_ctx)?;
    let output = ctx_from_py_any(output_ctx)?;
    let opts = coordinate_options_from_py(options)?;
    let result = py
        .detach(|| {
            coordinate_bm25_prune(
                &skills_arr,
                &catalog_val,
                &build_val,
                &index,
                query,
                &scoring,
                &output,
                &opts,
            )
        })
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &result)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(prune_catalog_bm25_and_retrieve_py, m)?)?;
    m.add_function(wrap_pyfunction!(recompose_and_retrieve_tools_py, m)?)?;
    m.add_function(wrap_pyfunction!(classify_and_count_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(search_skills_and_select_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_skill_node_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(coordinate_bm25_prune_py, m)?)?;
    Ok(())
}
