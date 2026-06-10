use crate::bm25_cohesion::{Bm25CohesionChunker, Bm25CohesionConfig};
use pyo3::prelude::*;

use super::{py_to_value, value_to_py};

#[pyfunction(name = "bm25_cohesion_chunk")]
fn bm25_cohesion_chunk_py(
    py: Python<'_>,
    text: &str,
    config: Option<Bound<'_, PyAny>>,
) -> PyResult<PyObject> {
    let cfg = match config {
        Some(obj) => Bm25CohesionConfig::from_partial(&py_to_value(obj)?),
        None => Bm25CohesionConfig::default(),
    };
    let chunker = Bm25CohesionChunker::new(cfg).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e)
    })?;
    let chunks: Vec<serde_json::Value> = chunker
        .chunk(text)
        .into_iter()
        .map(|c| {
            serde_json::json!({
                "text": c.text,
                "start_index": c.start_index,
                "end_index": c.end_index,
                "token_count": c.token_count,
            })
        })
        .collect();
    value_to_py(py, &serde_json::Value::Array(chunks))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(bm25_cohesion_chunk_py, m)?)?;
    Ok(())
}
