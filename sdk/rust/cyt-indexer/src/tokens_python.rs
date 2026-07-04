use crate::tiktoken::{self, AllowedSpecial};
use pyo3::prelude::*;

#[pyfunction(name = "count_tokens")]
fn count_tokens_py(py: Python<'_>, text: &str) -> PyResult<usize> {
    py.detach(|| tiktoken::count_tokens(text))
        .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}

#[pyfunction(name = "count_json_tokens")]
fn count_json_tokens_py(obj: Bound<'_, PyAny>) -> PyResult<usize> {
    let value = super::py_to_value(obj)?;
    tiktoken::count_json_tokens(&value).map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}

#[pyfunction(name = "count_tokens_batch")]
fn count_tokens_batch_py(py: Python<'_>, texts: Vec<String>) -> PyResult<Vec<usize>> {
    py.detach(|| {
        let boxed: Vec<Box<str>> = texts.into_iter().map(String::into_boxed_str).collect();
        let refs: Vec<&str> = boxed.iter().map(std::convert::AsRef::as_ref).collect();
        tiktoken::count_tokens_batch(&refs)
    })
    .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)
}
#[pyfunction(name = "configure_tokenizer_defaults")]
#[pyo3(signature = (encoding=None, allowed_special=None))]
fn configure_tokenizer_defaults_py(encoding: Option<String>, allowed_special: Option<String>) {
    let mut cfg = tiktoken::snapshot();
    if let Some(enc) = encoding {
        cfg.encoding = enc;
    }
    if let Some(mode) = allowed_special {
        cfg.allowed_special = match mode.to_ascii_lowercase().as_str() {
            "none" => AllowedSpecial::None,
            _ => AllowedSpecial::All,
        };
    }
    tiktoken::configure(cfg);
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(count_tokens_py, m)?)?;
    m.add_function(wrap_pyfunction!(count_tokens_batch_py, m)?)?;
    m.add_function(wrap_pyfunction!(count_json_tokens_py, m)?)?;
    m.add_function(wrap_pyfunction!(configure_tokenizer_defaults_py, m)?)?;
    Ok(())
}
