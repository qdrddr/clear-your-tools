use crate::pageindex::{
    EntryMetadata, PageIndexConfig, ReconstructOptions, SkillsIndex, build_chunk_variant,
    build_page_index_for_file, build_page_index_only, build_skills_index,
    build_skills_index_for_file, chunk_variant_valid, finalize_entry_metadata,
    get_content_retrieve_result, get_document, get_document_structure, get_line_content,
    get_line_content_from_spec, load_merged_document_json, md_to_tree, page_index_valid,
    parse_node_ids, reconstruct_skill_markdown, repair_skill_chunks, repair_skill_variant_chunks,
    spec_refs::OwnedSpecRefs, update_document_source_path, write_reconstructed_skill,
};
use crate::skills_builder::SkillsBuilder;
use crate::skills_io::{
    load_skills_index_from_dir, load_skills_index_from_entry, skills_index_from_decomposed_dir,
    write_skills_index,
};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::PathBuf;

use super::{py_to_value, value_to_py};

fn skills_index_to_py(py: Python<'_>, index: &SkillsIndex) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("documents", value_to_py(py, &index.documents_as_json())?)?;
    let files_dict = PyDict::new(py);
    for (k, v) in &index.files {
        files_dict.set_item(k, v)?;
    }
    dict.set_item("files", files_dict)?;
    Ok(dict.into())
}

fn page_index_config_from_py(config: Option<Bound<'_, PyAny>>) -> PyResult<PageIndexConfig> {
    match config {
        Some(obj) => Ok(PageIndexConfig::from_value(&py_to_value(obj)?)),
        None => Ok(PageIndexConfig::default()),
    }
}

#[pyfunction(name = "build_skills_index")]
fn build_skills_index_py(
    py: Python<'_>,
    skill_dirs: Vec<String>,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
    let index =
        build_skills_index(&dirs, &cfg).map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "write_skills_index")]
fn write_skills_index_py(index: Bound<'_, PyAny>, output_dir: String) -> PyResult<()> {
    let val = py_to_value(index)?;
    let mut skills = SkillsIndex::default();
    if let Some(docs) = val.get("documents").and_then(|v| v.as_object()) {
        for (doc_id, doc_val) in docs {
            if let Some(doc) = crate::pageindex::SkillDocument::from_json(doc_val) {
                skills.documents.insert(doc_id.clone(), doc);
            }
        }
    }
    if let Some(files) = val.get("files").and_then(|v| v.as_object()) {
        for (k, v) in files {
            if let Some(s) = v.as_str() {
                skills.files.insert(k.clone(), s.to_string());
            }
        }
    }
    write_skills_index(&skills, PathBuf::from(output_dir).as_path())
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}

#[pyfunction(name = "load_skills_index_from_dir")]
fn load_skills_index_from_dir_py(py: Python<'_>, catalog_dir: String) -> PyResult<Py<PyAny>> {
    let index = load_skills_index_from_dir(PathBuf::from(catalog_dir).as_path())
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "repair_skill_chunks")]
fn repair_skill_chunks_py(
    entry_dir: String,
    doc_id: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<()> {
    let cfg = page_index_config_from_py(config)?;
    repair_skill_chunks(PathBuf::from(entry_dir).as_path(), doc_id, &cfg)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}

#[pyfunction(name = "repair_skill_variant_chunks")]
#[pyo3(signature = (entry_dir, doc_id, pipeline, params_hash, config=None))]
fn repair_skill_variant_chunks_py(
    entry_dir: String,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<()> {
    let cfg = page_index_config_from_py(config)?;
    repair_skill_variant_chunks(
        PathBuf::from(entry_dir).as_path(),
        doc_id,
        pipeline,
        params_hash,
        &cfg,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}

#[pyfunction(name = "build_page_index_only")]
fn build_page_index_only_py(
    py: Python<'_>,
    skill_dirs: Vec<String>,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
    let index = build_page_index_only(&dirs, &cfg)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "build_chunk_variant")]
#[pyo3(signature = (entry_dir, doc_id, pipeline, params_hash, config=None))]
fn build_chunk_variant_py(
    py: Python<'_>,
    entry_dir: String,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let index = build_chunk_variant(
        PathBuf::from(entry_dir).as_path(),
        doc_id,
        pipeline,
        params_hash,
        &cfg,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "page_index_valid")]
#[pyo3(signature = (entry_dir, content_sha256))]
fn page_index_valid_py(entry_dir: String, content_sha256: &str) -> bool {
    page_index_valid(PathBuf::from(entry_dir).as_path(), content_sha256)
}

#[pyfunction(name = "chunk_variant_valid")]
fn chunk_variant_valid_py(
    entry_dir: String,
    doc_id: &str,
    pipeline: &str,
    params_hash: &str,
) -> bool {
    chunk_variant_valid(
        PathBuf::from(entry_dir).as_path(),
        doc_id,
        pipeline,
        params_hash,
    )
}

#[pyfunction(name = "load_skills_index_from_entry")]
#[pyo3(signature = (entry_dir, doc_id, chunk_dir=None))]
fn load_skills_index_from_entry_py(
    py: Python<'_>,
    entry_dir: String,
    doc_id: &str,
    chunk_dir: Option<String>,
) -> PyResult<Py<PyAny>> {
    let chunk_path = chunk_dir.map(PathBuf::from);
    let index = load_skills_index_from_entry(
        PathBuf::from(entry_dir).as_path(),
        doc_id,
        chunk_path.as_deref(),
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "load_merged_skill_document_json")]
#[pyo3(signature = (entry_dir, doc_id, chunk_dir=None))]
fn load_merged_skill_document_json_py(
    py: Python<'_>,
    entry_dir: String,
    doc_id: &str,
    chunk_dir: Option<String>,
) -> PyResult<Py<PyAny>> {
    let chunk_path = chunk_dir.map(PathBuf::from);
    let value = load_merged_document_json(
        PathBuf::from(entry_dir).as_path(),
        doc_id,
        chunk_path.as_deref(),
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &value)
}

#[pyfunction(name = "build_page_index_for_file")]
fn build_page_index_for_file_py(
    py: Python<'_>,
    source_path: String,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let index = build_page_index_for_file(PathBuf::from(source_path).as_path(), &cfg)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "build_skills_index_for_file")]
#[pyo3(signature = (source_path, *, config=None, pipeline="bm25", params_hash="default"))]
fn build_skills_index_for_file_py(
    py: Python<'_>,
    source_path: String,
    config: Option<Bound<'_, PyAny>>,
    pipeline: &str,
    params_hash: &str,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let index = build_skills_index_for_file(
        PathBuf::from(source_path).as_path(),
        &cfg,
        pipeline,
        params_hash,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "finalize_skill_document_json")]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (entry_dir, doc_id, *, pipeline, index_params, source_path, content_sha256=None, built_at=None))]
fn finalize_skill_document_json_py(
    py: Python<'_>,
    entry_dir: String,
    doc_id: &str,
    pipeline: &str,
    index_params: Bound<'_, PyAny>,
    source_path: &str,
    content_sha256: Option<&str>,
    built_at: Option<&str>,
) -> PyResult<Py<PyAny>> {
    let _ = (doc_id, content_sha256, built_at);
    let metadata = EntryMetadata {
        source_path: source_path.to_string(),
        pipeline: pipeline.to_string(),
        index_params: py_to_value(index_params)?,
    };
    let value = finalize_entry_metadata(PathBuf::from(entry_dir).as_path(), &metadata)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &value)
}

#[pyfunction(name = "update_skill_document_source_path")]
fn update_skill_document_source_path_py(
    py: Python<'_>,
    entry_dir: String,
    doc_id: &str,
    source_path: &str,
) -> PyResult<Py<PyAny>> {
    let value =
        update_document_source_path(PathBuf::from(entry_dir).as_path(), doc_id, source_path)
            .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &value)
}

#[pyfunction(name = "skills_index_from_decomposed_dir")]
fn skills_index_from_decomposed_dir_py(py: Python<'_>, dir: String) -> PyResult<Py<PyAny>> {
    let index = skills_index_from_decomposed_dir(PathBuf::from(dir).as_path())
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "md_to_tree")]
fn md_to_tree_py(
    py: Python<'_>,
    markdown_content: &str,
    source_path: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cfg = page_index_config_from_py(config)?;
    let result = md_to_tree(markdown_content, source_path, &cfg);
    value_to_py(
        py,
        &serde_json::json!({
            "doc_name": result.doc_name,
            "line_count": result.line_count,
            "structure": result.structure,
        }),
    )
}

fn documents_from_py(
    documents: Bound<'_, PyAny>,
) -> PyResult<std::collections::HashMap<String, crate::pageindex::SkillDocument>> {
    let val = py_to_value(documents)?;
    let mut out = std::collections::HashMap::new();
    let obj = val.as_object().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>("documents must be an object")
    })?;
    for (doc_id, doc_val) in obj {
        let doc = crate::pageindex::SkillDocument::from_json(doc_val).ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("invalid document {doc_id}"))
        })?;
        out.insert(doc_id.clone(), doc);
    }
    Ok(out)
}

fn skills_index_from_py(index_or_docs: Bound<'_, PyAny>) -> PyResult<SkillsIndex> {
    let val = py_to_value(index_or_docs)?;
    if val.get("documents").is_some() || val.get("files").is_some() {
        let mut skills = SkillsIndex::default();
        if let Some(docs) = val.get("documents").and_then(|v| v.as_object()) {
            for (doc_id, doc_val) in docs {
                if let Some(doc) = crate::pageindex::SkillDocument::from_json(doc_val) {
                    skills.documents.insert(doc_id.clone(), doc);
                }
            }
        }
        if let Some(files) = val.get("files").and_then(|v| v.as_object()) {
            for (k, v) in files {
                if let Some(s) = v.as_str() {
                    skills.files.insert(k.clone(), s.to_string());
                }
            }
        }
        return Ok(skills);
    }
    let mut skills = SkillsIndex::default();
    let obj = val.as_object().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>("documents must be an object")
    })?;
    for (doc_id, doc_val) in obj {
        let doc = crate::pageindex::SkillDocument::from_json(doc_val).ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("invalid document {doc_id}"))
        })?;
        skills.documents.insert(doc_id.clone(), doc);
    }
    Ok(skills)
}

#[pyfunction(name = "get_skill_document")]
fn get_skill_document_py(
    py: Python<'_>,
    documents: Bound<'_, PyAny>,
    doc_id: &str,
) -> PyResult<Py<PyAny>> {
    let docs = documents_from_py(documents)?;
    value_to_py(py, &get_document(&docs, doc_id))
}

#[pyfunction(name = "get_skill_structure")]
fn get_skill_structure_py(
    py: Python<'_>,
    documents: Bound<'_, PyAny>,
    doc_id: &str,
) -> PyResult<Py<PyAny>> {
    let docs = documents_from_py(documents)?;
    value_to_py(py, &get_document_structure(&docs, doc_id))
}

#[pyfunction(name = "get_skill_line_content_from_spec")]
fn get_skill_line_content_from_spec_py(
    py: Python<'_>,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_spec: &str,
) -> PyResult<Py<PyAny>> {
    let index = skills_index_from_py(index_or_docs)?;
    value_to_py(
        py,
        &get_line_content_from_spec(&index, doc_id, line_num_spec),
    )
}

#[pyclass(name = "ReconstructOptions", from_py_object)]
#[derive(Clone, Copy)]
struct PyReconstructOptions {
    #[pyo3(get, set)]
    keep_all_headers: bool,
}

#[pymethods]
impl PyReconstructOptions {
    #[new]
    #[pyo3(signature = (*, keep_all_headers=false))]
    const fn new(keep_all_headers: bool) -> Self {
        Self { keep_all_headers }
    }
}

fn reconstruct_options_from_py(opts: Option<Bound<'_, PyAny>>) -> PyResult<ReconstructOptions> {
    let Some(obj) = opts else {
        return Ok(ReconstructOptions::default());
    };
    if let Ok(extracted) = obj.extract::<PyReconstructOptions>() {
        return Ok(ReconstructOptions {
            keep_all_headers: extracted.keep_all_headers,
        });
    }
    let val = py_to_value(obj)?;
    Ok(ReconstructOptions {
        keep_all_headers: val
            .get("keep_all_headers")
            .or_else(|| val.get("keepAllHeaders"))
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
    })
}

#[pyfunction(name = "get_skill_content_retrieve_result")]
#[pyo3(signature = (index_or_docs, doc_id, *, line_num_specs=None, node_id_specs=None, chunk_id_specs=None, options=None))]
fn get_skill_content_retrieve_result_py(
    py: Python<'_>,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    chunk_id_specs: Option<Vec<String>>,
    options: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let index = skills_index_from_py(index_or_docs)?;
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs, chunk_id_specs);
    let opts = reconstruct_options_from_py(options)?;
    value_to_py(
        py,
        &get_content_retrieve_result(
            &index,
            doc_id,
            &specs.line_refs(),
            &specs.node_refs(),
            &specs.chunk_refs(),
            &opts,
        ),
    )
}

#[pyfunction(name = "reconstruct_skill_markdown")]
#[pyo3(signature = (index_or_docs, doc_id, *, line_num_specs=None, node_id_specs=None, chunk_id_specs=None, options=None))]
fn reconstruct_skill_markdown_py(
    py: Python<'_>,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    chunk_id_specs: Option<Vec<String>>,
    options: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let index = skills_index_from_py(index_or_docs)?;
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs, chunk_id_specs);
    let opts = reconstruct_options_from_py(options)?;
    let result = reconstruct_skill_markdown(
        &index,
        doc_id,
        &specs.line_refs(),
        &specs.node_refs(),
        &specs.chunk_refs(),
        &opts,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(
        py,
        &serde_json::json!({
            "markdown": result.markdown,
            "matched_node_ids": result.matched_node_ids,
            "matched_chunk_ids": result.matched_chunk_ids,
            "node_ids": result.node_ids,
            "output_rel_path": result.output_rel_path,
        }),
    )
}

#[pyfunction(name = "write_reconstructed_skill")]
#[pyo3(signature = (catalog_dir, index_or_docs, doc_id, *, line_num_specs=None, node_id_specs=None, chunk_id_specs=None, options=None))]
fn write_reconstructed_skill_py(
    catalog_dir: String,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    chunk_id_specs: Option<Vec<String>>,
    options: Option<Bound<'_, PyAny>>,
) -> PyResult<String> {
    let index = skills_index_from_py(index_or_docs)?;
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs, chunk_id_specs);
    let opts = reconstruct_options_from_py(options)?;
    let output = write_reconstructed_skill(
        PathBuf::from(catalog_dir).as_path(),
        &index,
        doc_id,
        &specs.line_refs(),
        &specs.node_refs(),
        &specs.chunk_refs(),
        &opts,
    )
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    Ok(output.display().to_string())
}

#[pyfunction(name = "get_skill_line_content")]
#[pyo3(signature = (index_or_docs, doc_id, *, line_num_specs=None, node_id_specs=None, chunk_id_specs=None))]
fn get_skill_line_content_py(
    py: Python<'_>,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    chunk_id_specs: Option<Vec<String>>,
) -> PyResult<Py<PyAny>> {
    let index = skills_index_from_py(index_or_docs)?;
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs, chunk_id_specs);
    value_to_py(
        py,
        &get_line_content(
            &index,
            doc_id,
            &specs.line_refs(),
            &specs.node_refs(),
            &specs.chunk_refs(),
        ),
    )
}

#[pyfunction(name = "parse_skill_chunk_ids")]
fn parse_skill_chunk_ids_py(py: Python<'_>, spec: &str) -> PyResult<Py<PyAny>> {
    let ids = crate::pageindex::parse_chunk_ids(spec)
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &serde_json::json!(ids))
}

#[pyfunction(name = "parse_skill_node_ids")]
fn parse_skill_node_ids_py(py: Python<'_>, spec: &str) -> PyResult<Py<PyAny>> {
    let ids = parse_node_ids(spec).map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    value_to_py(py, &serde_json::json!(ids))
}

#[pyclass(name = "SkillsBuilder")]
struct PySkillsBuilder {
    inner: SkillsBuilder,
}

#[pymethods]
impl PySkillsBuilder {
    #[new]
    #[pyo3(signature = (memory_only=true, output_dir=None))]
    fn new(memory_only: bool, output_dir: Option<String>) -> Self {
        Self {
            inner: SkillsBuilder::new(memory_only, output_dir.map(PathBuf::from)),
        }
    }

    fn build_from_dirs(
        &mut self,
        py: Python<'_>,
        skill_dirs: Vec<String>,
        config: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let cfg = page_index_config_from_py(config)?;
        let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
        let index = self
            .inner
            .build_from_dirs(&dirs, &cfg)
            .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
        skills_index_to_py(py, index)
    }

    fn build_from_file(
        &mut self,
        py: Python<'_>,
        source_path: String,
        config: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let cfg = page_index_config_from_py(config)?;
        let index = self
            .inner
            .build_from_file(PathBuf::from(source_path).as_path(), &cfg)
            .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
        skills_index_to_py(py, index)
    }

    fn write_catalog(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let index = self
            .inner
            .write_catalog()
            .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
        skills_index_to_py(py, index)
    }

    fn to_skills_index_json(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let val = self
            .inner
            .to_skills_index_json()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("index not built"))?;
        value_to_py(py, &val)
    }

    fn to_skills_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let val = self
            .inner
            .to_skills_dict()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("index not built"))?;
        value_to_py(py, &val)
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(build_skills_index_py, m)?)?;
    m.add_function(wrap_pyfunction!(write_skills_index_py, m)?)?;
    m.add_function(wrap_pyfunction!(load_skills_index_from_dir_py, m)?)?;
    m.add_function(wrap_pyfunction!(load_skills_index_from_entry_py, m)?)?;
    m.add_function(wrap_pyfunction!(repair_skill_chunks_py, m)?)?;
    m.add_function(wrap_pyfunction!(repair_skill_variant_chunks_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_page_index_only_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_page_index_for_file_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_skills_index_for_file_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_chunk_variant_py, m)?)?;
    m.add_function(wrap_pyfunction!(page_index_valid_py, m)?)?;
    m.add_function(wrap_pyfunction!(chunk_variant_valid_py, m)?)?;
    m.add_function(wrap_pyfunction!(load_merged_skill_document_json_py, m)?)?;
    m.add_function(wrap_pyfunction!(finalize_skill_document_json_py, m)?)?;
    m.add_function(wrap_pyfunction!(update_skill_document_source_path_py, m)?)?;
    m.add_function(wrap_pyfunction!(skills_index_from_decomposed_dir_py, m)?)?;
    m.add_function(wrap_pyfunction!(md_to_tree_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_document_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_structure_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_line_content_from_spec_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_line_content_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_content_retrieve_result_py, m)?)?;
    m.add_function(wrap_pyfunction!(reconstruct_skill_markdown_py, m)?)?;
    m.add_function(wrap_pyfunction!(write_reconstructed_skill_py, m)?)?;
    m.add_function(wrap_pyfunction!(parse_skill_chunk_ids_py, m)?)?;
    m.add_function(wrap_pyfunction!(parse_skill_node_ids_py, m)?)?;
    m.add_class::<PyReconstructOptions>()?;
    m.add_class::<PySkillsBuilder>()?;
    Ok(())
}
