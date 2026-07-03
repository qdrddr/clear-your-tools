use crate::bm25_search::{
    self, ScoreCatalogOptions, batch_reconstruct_skill_matches, bm25_frontmatter_gate,
    bm25_search_skill_chunks, collect_catalog_documents, exp_similarity, greedy_select_skill_items,
    score_catalog_in_place,
};
use pyo3::prelude::*;
use serde_json::Value;

use super::{py_to_value, value_to_py};

#[pyfunction(name = "configure_bm25_defaults")]
#[pyo3(signature = (index_dir=None, stem_language=None, stopwords=None, use_stopwords=None, k1=None, b=None, mmap=None))]
fn configure_bm25_defaults_py(
    index_dir: Option<String>,
    stem_language: Option<String>,
    stopwords: Option<String>,
    use_stopwords: Option<bool>,
    k1: Option<f64>,
    b: Option<f64>,
    mmap: Option<bool>,
) {
    let mut cfg = bm25_search::snapshot();
    if let Some(dir) = index_dir {
        cfg.index_dir = dir.into();
    }
    if let Some(v) = stem_language {
        cfg.stem_language = v;
    }
    if let Some(v) = stopwords {
        cfg.stopwords = v;
    }
    if let Some(v) = use_stopwords {
        cfg.use_stopwords = v;
    }
    if let Some(v) = k1 {
        cfg.k1 = v;
    }
    if let Some(v) = b {
        cfg.b = v;
    }
    if let Some(v) = mmap {
        cfg.mmap = v;
    }
    bm25_search::configure(&cfg);
}

#[pyfunction(name = "bm25_catalog_fingerprint")]
fn bm25_catalog_fingerprint_py(data: Bound<'_, PyAny>) -> PyResult<String> {
    let value = py_to_value(data)?;
    let cfg = bm25_search::snapshot();
    let docs = collect_catalog_documents(&value);
    Ok(bm25_search::catalog_fingerprint(
        &docs,
        &cfg.stem_language,
        &cfg.stopwords,
    ))
}

#[pyfunction(name = "bm25_score_catalog")]
#[pyo3(signature = (data, query, prune_json_threshold=None, prune_md_threshold=None, prune_enums=true))]
fn bm25_score_catalog_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    query: &str,
    prune_json_threshold: Option<f64>,
    prune_md_threshold: Option<f64>,
    prune_enums: bool,
) -> PyResult<Py<PyAny>> {
    let mut value = py_to_value(data)?;
    let options = ScoreCatalogOptions {
        prune_json_threshold,
        prune_md_threshold,
        prune_enums,
        ..ScoreCatalogOptions::default()
    };
    score_catalog_in_place(&mut value, query, &options)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &value)
}

#[pyfunction(name = "bm25_frontmatter_gate")]
#[pyo3(signature = (entries, query, upper_limit=0.4))]
fn bm25_frontmatter_gate_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    query: &str,
    upper_limit: f64,
) -> PyResult<Py<PyAny>> {
    let entries_val = py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let (excluded, trace) = bm25_frontmatter_gate(&arr, query, upper_limit)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    let excluded_json: Vec<Value> = excluded
        .into_iter()
        .map(|(entry_dir, doc_id)| serde_json::json!({ "entry_dir": entry_dir, "doc_id": doc_id }))
        .collect();
    value_to_py(
        py,
        &serde_json::json!({ "excluded": excluded_json, "trace": trace }),
    )
}

#[pyfunction(name = "bm25_search_skill_chunks")]
#[pyo3(signature = (entries, query, threshold=0.5, excluded=None))]
fn bm25_search_skill_chunks_py(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
    query: &str,
    threshold: f64,
    excluded: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let entries_val = py_to_value(entries)?;
    let arr = entries_val.as_array().cloned().unwrap_or_default();
    let mut excluded_set = std::collections::HashSet::new();
    if let Some(ex) = excluded {
        let ex_val = py_to_value(ex)?;
        if let Some(items) = ex_val.as_array() {
            for item in items {
                if let Some(obj) = item.as_object() {
                    let entry_dir = obj
                        .get("entry_dir")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    let doc_id = obj
                        .get("doc_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    excluded_set.insert((entry_dir, doc_id));
                }
            }
        }
    }
    let result = bm25_search_skill_chunks(&arr, query, threshold, &excluded_set)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &result)
}

#[pyfunction(name = "exp_similarity")]
fn exp_similarity_py(raw: f64) -> f64 {
    exp_similarity(raw)
}

#[pyfunction(name = "batch_reconstruct_skill_matches")]
fn batch_reconstruct_skill_matches_py(
    py: Python<'_>,
    groups: Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let groups_val = py_to_value(groups)?;
    let arr = groups_val.as_array().cloned().unwrap_or_default();
    let matches = batch_reconstruct_skill_matches(&arr)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &serde_json::json!(matches))
}

#[pyfunction(name = "greedy_select_skill_items")]
#[pyo3(signature = (survivors, item_kind="node", max_tokens=0))]
fn greedy_select_skill_items_py(
    py: Python<'_>,
    survivors: Bound<'_, PyAny>,
    item_kind: &str,
    max_tokens: i64,
) -> PyResult<Py<PyAny>> {
    let survivors_val = py_to_value(survivors)?;
    let arr = survivors_val.as_array().cloned().unwrap_or_default();
    let result = greedy_select_skill_items(&arr, item_kind, max_tokens)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &result)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(configure_bm25_defaults_py, m)?)?;
    m.add_function(wrap_pyfunction!(bm25_catalog_fingerprint_py, m)?)?;
    m.add_function(wrap_pyfunction!(bm25_score_catalog_py, m)?)?;
    m.add_function(wrap_pyfunction!(bm25_frontmatter_gate_py, m)?)?;
    m.add_function(wrap_pyfunction!(bm25_search_skill_chunks_py, m)?)?;
    m.add_function(wrap_pyfunction!(exp_similarity_py, m)?)?;
    m.add_function(wrap_pyfunction!(batch_reconstruct_skill_matches_py, m)?)?;
    m.add_function(wrap_pyfunction!(greedy_select_skill_items_py, m)?)?;
    Ok(())
}
