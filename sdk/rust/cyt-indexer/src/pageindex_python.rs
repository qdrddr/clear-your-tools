use crate::pageindex::{
    build_skills_index, get_document, get_document_structure, get_line_content_from_spec,
    md_to_tree, PageIndexConfig, SkillsIndex,
};
use crate::skills_builder::SkillsBuilder;
use crate::skills_io::{
    load_skills_index_from_dir, skills_index_from_decomposed_dir, write_skills_index,
};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::PathBuf;

use super::{py_to_value, value_to_py};

fn skills_index_to_py(py: Python<'_>, index: &SkillsIndex) -> PyResult<PyObject> {
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
) -> PyResult<PyObject> {
    let cfg = page_index_config_from_py(config)?;
    let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
    let index = build_skills_index(&dirs, &cfg).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
    })?;
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
    write_skills_index(&skills, PathBuf::from(output_dir).as_path()).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
    })
}

#[pyfunction(name = "load_skills_index_from_dir")]
fn load_skills_index_from_dir_py(py: Python<'_>, catalog_dir: String) -> PyResult<PyObject> {
    let index = load_skills_index_from_dir(PathBuf::from(catalog_dir).as_path()).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
    })?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "skills_index_from_decomposed_dir")]
fn skills_index_from_decomposed_dir_py(py: Python<'_>, dir: String) -> PyResult<PyObject> {
    let index = skills_index_from_decomposed_dir(PathBuf::from(dir).as_path()).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
    })?;
    skills_index_to_py(py, &index)
}

#[pyfunction(name = "md_to_tree")]
fn md_to_tree_py(
    py: Python<'_>,
    markdown_content: &str,
    source_path: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<PyObject> {
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

fn documents_from_py(documents: Bound<'_, PyAny>) -> PyResult<std::collections::HashMap<String, crate::pageindex::SkillDocument>> {
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
fn get_skill_document_py(py: Python<'_>, documents: Bound<'_, PyAny>, doc_id: &str) -> PyResult<PyObject> {
    let docs = documents_from_py(documents)?;
    value_to_py(py, &get_document(&docs, doc_id))
}

#[pyfunction(name = "get_skill_structure")]
fn get_skill_structure_py(py: Python<'_>, documents: Bound<'_, PyAny>, doc_id: &str) -> PyResult<PyObject> {
    let docs = documents_from_py(documents)?;
    value_to_py(py, &get_document_structure(&docs, doc_id))
}

#[pyfunction(name = "get_skill_line_content_from_spec")]
fn get_skill_line_content_from_spec_py(
    py: Python<'_>,
    index_or_docs: Bound<'_, PyAny>,
    doc_id: &str,
    line_num_spec: &str,
) -> PyResult<PyObject> {
    let index = skills_index_from_py(index_or_docs)?;
    value_to_py(py, &get_line_content_from_spec(&index, doc_id, line_num_spec))
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
            inner: SkillsBuilder::new(
                memory_only,
                output_dir.map(PathBuf::from),
            ),
        }
    }

    fn build_from_dirs(
        &mut self,
        py: Python<'_>,
        skill_dirs: Vec<String>,
        config: Option<Bound<'_, PyAny>>,
    ) -> PyResult<PyObject> {
        let cfg = page_index_config_from_py(config)?;
        let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
        let index = self.inner.build_from_dirs(&dirs, &cfg).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
        })?;
        skills_index_to_py(py, index)
    }

    fn write_catalog(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let index = self.inner.write_catalog().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
        })?;
        skills_index_to_py(py, index)
    }

    fn to_skills_index_json(&self, py: Python<'_>) -> PyResult<PyObject> {
        let val = self
            .inner
            .to_skills_index_json()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("index not built"))?;
        value_to_py(py, &val)
    }

    fn to_skills_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
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
    m.add_function(wrap_pyfunction!(skills_index_from_decomposed_dir_py, m)?)?;
    m.add_function(wrap_pyfunction!(md_to_tree_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_document_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_structure_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_skill_line_content_from_spec_py, m)?)?;
    m.add_class::<PySkillsBuilder>()?;
    Ok(())
}
