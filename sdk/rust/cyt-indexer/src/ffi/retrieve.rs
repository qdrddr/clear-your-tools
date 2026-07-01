//! Catalog retrieval and decomposed catalog FFI exports.

use crate::ffi::error::{CYT_ERR_IO, CYT_ERR_NULL_PTR, clear_error, set_error};
use crate::ffi::json_util::{
    c_str_to_str, catalog_index_from_json, parse_json_cstr, parse_policy_context, run_ffi,
    write_json_out, write_optional_string_out,
};
use crate::policies::policy_context_from_values;
use crate::retrieve::{
    DecomposedCatalog, ProcessGroupsOptions, RemovedChunksOptions, RetrieveOptions,
    build_process_groups_options, chunk_survivor_key, load_catalog_from_dir,
    process_groups_options_from_fields, removed_chunks, resolve_build_catalog, retrieve_core,
    retrieve_tools_from_catalog,
};
use serde_json::{Map, Value, json};
use std::collections::HashMap;
use std::os::raw::{c_char, c_int, c_long};

/// Opaque in-memory decomposed catalog handle.
pub struct CytDecomposedCatalog {
    pub(crate) inner: DecomposedCatalog,
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_new(out: *mut *mut CytDecomposedCatalog) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        unsafe {
            *out = Box::into_raw(Box::new(CytDecomposedCatalog {
                inner: DecomposedCatalog::default(),
            }));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_free(catalog: *mut CytDecomposedCatalog) {
    if !catalog.is_null() {
        let _ = Box::from_raw(catalog);
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_from_catalog_index(
    index_json: *const c_char,
    out: *mut *mut CytDecomposedCatalog,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_json, "index_json")? };
        let idx = catalog_index_from_json(&val);
        unsafe {
            *out = Box::into_raw(Box::new(CytDecomposedCatalog {
                inner: DecomposedCatalog::from_catalog_index(&idx),
            }));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_from_catalog_dict(
    data_json: *const c_char,
    out: *mut *mut CytDecomposedCatalog,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(data_json, "data_json")? };
        unsafe {
            *out = Box::into_raw(Box::new(CytDecomposedCatalog {
                inner: DecomposedCatalog::from_catalog_dict(&val),
            }));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_has_json(
    catalog: *const CytDecomposedCatalog,
    key: *const c_char,
) -> c_int {
    if catalog.is_null() {
        set_error("null pointer: catalog");
        return CYT_ERR_NULL_PTR;
    }
    let key_str = match unsafe { c_str_to_str(key, "key") } {
        Ok(s) => s,
        Err(code) => return code,
    };
    i32::from(unsafe { (*catalog).inner.has_json(key_str) })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_decomposed_catalog_get_json(
    catalog: *const CytDecomposedCatalog,
    key: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if catalog.is_null() {
            set_error("null pointer: catalog");
            return Err(CYT_ERR_NULL_PTR);
        }
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let key_str = unsafe { c_str_to_str(key, "key")? };
        if let Some(v) = unsafe { (*catalog).inner.get_json(key_str) } {
            unsafe { write_json_out(v, out)? };
        } else {
            unsafe { *out = std::ptr::null_mut() };
            clear_error();
        }
        Ok(())
    })
}

fn process_groups_from_policy_json(policy: &Value) -> ProcessGroupsOptions {
    let prune_optional_tools = Some(
        policy
            .get("prune_optional_tools")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default(),
    );

    let system_preserve = policy.get("system_preserve").and_then(|v| {
        if v.is_null() {
            None
        } else {
            Some(
                v.as_array()
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect(),
            )
        }
    });

    let mcp_preserve = policy.get("mcp_preserve").and_then(|v| {
        if v.is_null() {
            None
        } else {
            Some(
                v.as_array()
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect(),
            )
        }
    });

    let mut required_by_tool = None;
    let mut required_enum_values_by_tool = None;
    for key in ["required_by_tool", "required_enum_values_by_tool"] {
        if let Some(map) = policy.get(key).and_then(Value::as_object) {
            let mut parsed: HashMap<String, Vec<String>> = HashMap::new();
            for (k, v) in map {
                parsed.insert(
                    k.clone(),
                    v.as_array()
                        .cloned()
                        .unwrap_or_default()
                        .into_iter()
                        .filter_map(|item| item.as_str().map(str::to_string))
                        .collect(),
                );
            }
            if key == "required_by_tool" {
                required_by_tool = Some(parsed);
            } else {
                required_enum_values_by_tool = Some(parsed);
            }
            break;
        }
    }

    process_groups_options_from_fields(
        system_preserve,
        mcp_preserve,
        required_by_tool,
        required_enum_values_by_tool,
        prune_optional_tools,
    )
}

fn decomposed_from_json_files(val: &Value) -> DecomposedCatalog {
    if val
        .as_object()
        .is_some_and(|o| o.values().all(Value::is_object))
    {
        let map: HashMap<String, Value> = val
            .as_object()
            .into_iter()
            .flatten()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        DecomposedCatalog::from_json_files(map)
    } else if val.get("json").is_some() {
        DecomposedCatalog::from_catalog_dict(val)
    } else {
        DecomposedCatalog::from_catalog_index(&catalog_index_from_json(val))
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_retrieve_core(
    data_json: *const c_char,
    store_json: *const c_char,
    survivor_json: *const c_char,
    apply_decomposed_score_filter: c_int,
    policy_options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data_val = unsafe { parse_json_cstr(data_json, "data_json")? };
        let store_val = if store_json.is_null() {
            Value::Object(Map::new())
        } else {
            unsafe { parse_json_cstr(store_json, "store_json")? }
        };
        let survivor_val = if survivor_json.is_null() {
            Value::Object(Map::new())
        } else {
            unsafe { parse_json_cstr(survivor_json, "survivor_json")? }
        };
        let mut store = decomposed_from_json_files(&store_val);
        let survivor = decomposed_from_json_files(&survivor_val);
        let process_groups = if policy_options_json.is_null() {
            ProcessGroupsOptions::default()
        } else {
            let policy = unsafe { parse_json_cstr(policy_options_json, "policy_options_json")? };
            process_groups_from_policy_json(&policy)
        };
        let opts = RetrieveOptions {
            apply_decomposed_score_filter: apply_decomposed_score_filter != 0,
            process_groups,
        };
        let result = retrieve_core(&data_val, &mut store, &survivor, &opts);
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_load_catalog(dir_path: *const c_char, out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dir = unsafe { c_str_to_str(dir_path, "dir_path")? };
        let catalog = load_catalog_from_dir(dir).map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_json_out(&catalog, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_chunk_survivor_key(
    item_json: *const c_char,
    section: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        let section_str = unsafe { c_str_to_str(section, "section")? };
        unsafe { write_optional_string_out(chunk_survivor_key(&item, section_str), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_removed_chunks(
    full_catalog_json: *const c_char,
    surviving_json: *const c_char,
    apply_decomposed_score_filter: c_int,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let full = unsafe { parse_json_cstr(full_catalog_json, "full_catalog_json")? };
        let surviving = unsafe { parse_json_cstr(surviving_json, "surviving_json")? };
        let removed = removed_chunks(
            &full,
            &surviving,
            &RemovedChunksOptions {
                apply_decomposed_score_filter: apply_decomposed_score_filter != 0,
            },
        );
        unsafe { write_json_out(&removed, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_retrieve_tools(
    data_json: *const c_char,
    catalog: *mut CytDecomposedCatalog,
    catalog_index_json: *const c_char,
    apply_decomposed_score_filter: c_int,
    preserve_values_json: *const c_char,
    ctx_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        if catalog.is_null() {
            set_error("null pointer: catalog");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data_val = unsafe { parse_json_cstr(data_json, "data_json")? };
        let policy_ctx = if ctx_json.is_null() {
            policy_context_from_values(&Value::Object(Map::new()))
        } else {
            let ctx_val = unsafe { parse_json_cstr(ctx_json, "ctx_json")? };
            parse_policy_context(&ctx_val)
        };

        let build_catalog = if catalog_index_json.is_null() {
            resolve_build_catalog(&json!({}), &data_val)
        } else {
            let idx_val = unsafe { parse_json_cstr(catalog_index_json, "catalog_index_json")? };
            catalog_index_from_json(&idx_val).to_catalog_dict()
        };

        let preserve_set = if preserve_values_json.is_null() {
            None
        } else {
            let val = unsafe { parse_json_cstr(preserve_values_json, "preserve_values_json")? };
            Some(
                val.as_array()
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect::<Vec<_>>(),
            )
        };

        let catalog_ref = unsafe { &mut *catalog };
        let process_groups = build_process_groups_options(
            &policy_ctx,
            &build_catalog,
            &catalog_ref.inner,
            preserve_set,
        );
        let result = retrieve_tools_from_catalog(
            &policy_ctx,
            &data_val,
            &build_catalog,
            &mut catalog_ref.inner,
            &RetrieveOptions {
                apply_decomposed_score_filter: apply_decomposed_score_filter != 0,
                process_groups,
            },
        );
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_retrieve_catalog_tool_count(data_json: *const c_char) -> c_long {
    unsafe { crate::ffi::catalog::cyt_catalog_tool_count(data_json) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_resolve_build_catalog(
    catalog_json: *const c_char,
    survivor_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let catalog_val = unsafe { parse_json_cstr(catalog_json, "catalog_json")? };
        let survivor = unsafe { parse_json_cstr(survivor_json, "survivor_json")? };
        let build = if catalog_val.get("tools").is_some() {
            catalog_index_from_json(&catalog_val).to_catalog_dict()
        } else {
            resolve_build_catalog(&catalog_val, &survivor)
        };
        unsafe { write_json_out(&build, out)? };
        Ok(())
    })
}
