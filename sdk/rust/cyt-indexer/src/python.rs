use crate::build::{build_catalog_index, catalog_tool_count};
use crate::retrieve::{
    load_catalog_from_dir, retrieve_core, DecomposedCatalog, ProcessGroupsOptions, RetrieveOptions,
};
use crate::tokens::{compact_json, count_json_tokens, count_tokens};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;

fn value_to_py(py: Python<'_>, value: &Value) -> PyResult<PyObject> {
    let json_str = serde_json::to_string(value)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let json_mod = py.import("json")?;
    Ok(json_mod.call_method1("loads", (json_str,))?.into())
}

fn py_to_value(obj: Bound<'_, PyAny>) -> PyResult<Value> {
    let json_mod = obj.py().import("json")?;
    let dumped = json_mod.call_method1("dumps", (obj,))?;
    let s: String = dumped.extract()?;
    serde_json::from_str(&s)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

#[pyfunction]
fn compact_json_py(obj: Bound<'_, PyAny>) -> PyResult<String> {
    Ok(compact_json(&py_to_value(obj)?))
}

#[pyfunction]
fn count_tokens_py(text: &str) -> PyResult<usize> {
    Ok(count_tokens(text))
}

#[pyfunction]
fn count_json_tokens_py(obj: Bound<'_, PyAny>) -> PyResult<usize> {
    Ok(count_json_tokens(&py_to_value(obj)?))
}

#[pyfunction]
fn catalog_tool_count_py(data: Bound<'_, PyAny>) -> PyResult<usize> {
    Ok(catalog_tool_count(&py_to_value(data)?))
}

#[pyfunction]
fn build_catalog_index_py(
    tools: Bound<'_, PyAny>,
    all_enums: Bound<'_, PyAny>,
) -> PyResult<PyObject> {
    let py = tools.py();
    let tools_val = py_to_value(tools)?;
    let enums_val = py_to_value(all_enums)?;
    let tools_arr = tools_val.as_array().cloned().unwrap_or_default();
    let enums_arr = enums_val.as_array().cloned().unwrap_or_default();
    let index = build_catalog_index(&tools_arr, &enums_arr);

    let dict = PyDict::new(py);
    dict.set_item("tools", value_to_py(py, &Value::Array(index.tools))?)?;
    let files_dict = PyDict::new(py);
    for (k, v) in &index.files {
        files_dict.set_item(k, v)?;
    }
    dict.set_item("files", files_dict)?;
    Ok(dict.into())
}

fn process_groups_from_policy_dict(
    policy: Option<Bound<'_, PyDict>>,
) -> PyResult<ProcessGroupsOptions> {
    let Some(policy) = policy else {
        return Ok(ProcessGroupsOptions::default());
    };
    let prune_optional_tools = policy
        .get_item("prune_optional_tools")?
        .map(|v| v.extract::<Vec<String>>())
        .transpose()?
        .unwrap_or_default()
        .into_iter()
        .collect();
    let system_preserve = policy
        .get_item("system_preserve")?
        .map(|v| v.extract::<Vec<String>>())
        .transpose()?
        .map(|v| v.into_iter().collect());
    let mcp_preserve = policy
        .get_item("mcp_preserve")?
        .map(|v| v.extract::<Vec<String>>())
        .transpose()?
        .map(|v| v.into_iter().collect());
    let mut required_by_tool = std::collections::HashMap::new();
    if let Some(item) = policy.get_item("required_by_tool")? {
        if let Ok(dict) = item.downcast_into::<PyDict>() {
            required_by_tool = dict_to_required_by_tool(dict)?;
        }
    }
    Ok(ProcessGroupsOptions {
        system_preserve,
        mcp_preserve,
        required_by_tool,
        prune_optional_tools,
    })
}

#[pyfunction]
#[pyo3(signature = (data, store_json_files, survivor_json_files, apply_decomposed_score_filter=true, policy_options=None))]
fn retrieve_core_py(
    py: Python<'_>,
    data: Bound<'_, PyAny>,
    store_json_files: Bound<'_, PyAny>,
    survivor_json_files: Bound<'_, PyAny>,
    apply_decomposed_score_filter: bool,
    policy_options: Option<Bound<'_, PyDict>>,
) -> PyResult<PyObject> {
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
    d: Bound<'_, PyDict>,
) -> PyResult<std::collections::HashMap<String, std::collections::HashSet<String>>> {
    let mut map = std::collections::HashMap::new();
    for (k, v) in d.iter() {
        let key: String = k.extract()?;
        let items: Vec<String> = v.extract()?;
        map.insert(key, items.into_iter().collect());
    }
    Ok(map)
}

#[pyfunction]
fn load_catalog_py(dir_path: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let catalog = load_catalog_from_dir(dir_path)
            .map_err(PyErr::new::<pyo3::exceptions::PyOSError, _>)?;
        value_to_py(py, &catalog)
    })
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compact_json_py, m)?)?;
    m.add_function(wrap_pyfunction!(count_tokens_py, m)?)?;
    m.add_function(wrap_pyfunction!(count_json_tokens_py, m)?)?;
    m.add_function(wrap_pyfunction!(catalog_tool_count_py, m)?)?;
    m.add_function(wrap_pyfunction!(build_catalog_index_py, m)?)?;
    m.add_function(wrap_pyfunction!(retrieve_core_py, m)?)?;
    m.add_function(wrap_pyfunction!(load_catalog_py, m)?)?;
    Ok(())
}
