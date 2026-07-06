//! Policy and chunk classification FFI exports (mirrors `policies_python.rs`).

use crate::ffi::error::{CYT_ERR_INVALID_ARG, CYT_ERR_NULL_PTR, clear_error, set_error};
use crate::ffi::json_util::{
    c_str_to_str, catalog_index_from_json, ffi_guard, json_array_or_empty, json_object_or_empty,
    optional_string_set_from_json, parse_json_cstr, parse_policy_context, run_ffi, write_json_out,
    write_string_result,
};
use crate::policies::{self, PolicyContext, parse_tool_policy, policy_context_from_values};
use crate::runtime_config;
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};
use std::os::raw::{c_char, c_int};

fn parse_ctx_json(ctx_json: *const c_char) -> Result<PolicyContext, c_int> {
    if ctx_json.is_null() {
        return Ok(parse_policy_context(&Value::Object(Map::new())));
    }
    let val = unsafe { parse_json_cstr(ctx_json, "ctx_json")? };
    Ok(parse_policy_context(&val))
}

fn hashset_to_json(set: HashSet<String>) -> Value {
    Value::Array(set.into_iter().map(Value::String).collect())
}

fn policy_context_to_json(ctx: &PolicyContext) -> Value {
    let mut obj = serde_json::Map::new();
    obj.insert(
        "system_policy".into(),
        Value::String(ctx.system_policy.as_str().to_string()),
    );
    obj.insert(
        "mcp_policy".into(),
        Value::String(ctx.mcp_policy.as_str().to_string()),
    );
    obj.insert(
        "per_tool".into(),
        Value::Object(
            ctx.per_tool
                .iter()
                .map(|(k, v)| (k.clone(), Value::String(v.as_str().to_string())))
                .collect(),
        ),
    );
    if let Some(kind) = ctx.tool_kind_override {
        obj.insert("tool_kind".into(), Value::String(kind.as_str().to_string()));
    }
    Value::Object(obj)
}

macro_rules! bool_item_fn {
    ($name:ident, $rust_fn:expr) => {
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(item_json: *const c_char) -> c_int {
            ffi_guard(|| {
                let item = parse_json_cstr(item_json, "item_json")?;
                clear_error();
                Ok(i32::from($rust_fn(&item)))
            })
            .unwrap_or_else(|code| code)
        }
    };
}

macro_rules! bool_ctx_fn {
    ($name:ident, $rust_fn:ident) => {
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(ctx_json: *const c_char) -> c_int {
            ffi_guard(|| {
                let ctx = parse_ctx_json(ctx_json)?;
                clear_error();
                Ok(i32::from(policies::$rust_fn(&ctx)))
            })
            .unwrap_or_else(|code| code)
        }
    };
}

macro_rules! bool_ctx_tool_fn {
    ($name:ident, $rust_fn:ident) => {
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(ctx_json: *const c_char, tool_id: *const c_char) -> c_int {
            ffi_guard(|| {
                let ctx = parse_ctx_json(ctx_json)?;
                let tool = c_str_to_str(tool_id, "tool_id")?;
                clear_error();
                Ok(i32::from(policies::$rust_fn(&ctx, tool)))
            })
            .unwrap_or_else(|code| code)
        }
    };
}

macro_rules! json_array_fn {
    ($name:ident, $rust_fn:ident) => {
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(input_json: *const c_char, out: *mut *mut c_char) -> c_int {
            run_ffi(|| {
                if out.is_null() {
                    set_error("null pointer: out");
                    return Err(CYT_ERR_NULL_PTR);
                }
                let arr =
                    json_array_or_empty(&unsafe { parse_json_cstr(input_json, "input_json")? });
                unsafe { write_json_out(&Value::Array(policies::$rust_fn(&arr)), out)? };
                Ok(())
            })
        }
    };
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tool_policies(out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let list: Vec<Value> = policies::tool_policy_strings()
            .into_iter()
            .map(|s| Value::String(s.to_string()))
            .collect();
        unsafe { write_json_out(&Value::Array(list), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_policy_context_from_values(
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(config_json, "config_json")? };
        let ctx = policy_context_from_values(&val);
        unsafe { write_json_out(&policy_context_to_json(&ctx), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_effective_policy(
    ctx_json: *const c_char,
    tool_id: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let tool = unsafe { c_str_to_str(tool_id, "tool_id")? };
        unsafe {
            write_string_result(policies::effective_policy(&ctx, tool).as_str(), out)?;
        }
        Ok(())
    })
}

bool_ctx_tool_fn!(cyt_tool_pass_through, tool_pass_through);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_batch_tool_pass_through(
    ctx_json: *const c_char,
    tool_ids_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let arr = json_array_or_empty(&unsafe { parse_json_cstr(tool_ids_json, "tool_ids_json")? });
        let ids: Vec<&str> = arr.iter().filter_map(|v| v.as_str()).collect();
        let results: Vec<Value> = policies::batch_tool_pass_through(&ctx, &ids)
            .into_iter()
            .map(Value::Bool)
            .collect();
        unsafe { write_json_out(&Value::Array(results), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_partition_catalog(
    data_json: *const c_char,
    ctx_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        let ctx = parse_ctx_json(ctx_json)?;
        let (processed, pinned) = policies::partition_catalog(&data, &ctx);
        unsafe {
            write_json_out(&json!({ "processed": processed, "pinned": pinned }), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_merge_catalog(
    processed_json: *const c_char,
    pinned_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let processed = unsafe { parse_json_cstr(processed_json, "processed_json")? };
        let pinned = unsafe { parse_json_cstr(pinned_json, "pinned_json")? };
        unsafe { write_json_out(&policies::merge_catalog(&processed, &pinned), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_catalog_needs_partition(
    data_json: *const c_char,
    ctx_json: *const c_char,
) -> c_int {
    ffi_guard(|| {
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        let ctx = parse_ctx_json(ctx_json)?;
        clear_error();
        Ok(i32::from(policies::catalog_needs_partition(&data, &ctx)))
    })
    .unwrap_or_else(|code| code)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_catalog_needs_pruned_recompose(
    data_json: *const c_char,
    ctx_json: *const c_char,
) -> c_int {
    ffi_guard(|| {
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        let ctx = parse_ctx_json(ctx_json)?;
        clear_error();
        Ok(i32::from(policies::catalog_needs_pruned_recompose(
            &data, &ctx,
        )))
    })
    .unwrap_or_else(|code| code)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_request_pass_through(
    ctx_json: *const c_char,
    tools_json: *const c_char,
) -> c_int {
    ffi_guard(|| {
        let ctx = parse_ctx_json(ctx_json)?;
        let tools = json_array_or_empty(&unsafe { parse_json_cstr(tools_json, "tools_json")? });
        clear_error();
        Ok(i32::from(policies::request_pass_through(&ctx, &tools)))
    })
    .unwrap_or_else(|code| code)
}

bool_ctx_fn!(cyt_full_pass_through, full_pass_through);
bool_item_fn!(
    cyt_is_decomposed_tool_root_chunk,
    policies::is_decomposed_tool_root_chunk
);
bool_item_fn!(
    cyt_is_decomposed_optional_property_chunk,
    policies::is_decomposed_optional_property_chunk
);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_filter_recompose_json_entries(
    json_list_json: *const c_char,
    ctx_json: *const c_char,
    rerank_score: f64,
    use_default_rerank_score: c_int,
    llm_selected_paths_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let arr =
            json_array_or_empty(&unsafe { parse_json_cstr(json_list_json, "json_list_json")? });
        let score = if use_default_rerank_score != 0 {
            runtime_config::rerank_score()
        } else {
            rerank_score
        };
        let paths = if llm_selected_paths_json.is_null() {
            None
        } else {
            let val =
                unsafe { parse_json_cstr(llm_selected_paths_json, "llm_selected_paths_json")? };
            optional_string_set_from_json(&val)
        };
        let filtered = policies::filter_recompose_json_entries(&ctx, &arr, score, paths.as_ref());
        unsafe { write_json_out(&Value::Array(filtered), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_mitigate_empty_optional_properties(
    entries_json: *const c_char,
    catalog_index_json: *const c_char,
    ctx_json: *const c_char,
    post_rerank_scored_json: *const c_char,
    pipeline_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let entries =
            json_array_or_empty(&unsafe { parse_json_cstr(entries_json, "entries_json")? });
        let index = catalog_index_from_json(&unsafe {
            parse_json_cstr(catalog_index_json, "catalog_index_json")?
        });
        let scored = if post_rerank_scored_json.is_null() {
            None
        } else {
            Some(unsafe { parse_json_cstr(post_rerank_scored_json, "post_rerank_scored_json")? })
        };
        let pipeline_val = unsafe { parse_json_cstr(pipeline_json, "pipeline_json")? };
        let pipeline: Vec<String> = json_array_or_empty(&pipeline_val)
            .into_iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
        let result = policies::mitigate_empty_optional_properties(
            &ctx,
            &entries,
            &index,
            scored.as_ref(),
            &pipeline,
        );
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_append_description_reinstate_entries(
    entries_json: *const c_char,
    build_catalog_json: *const c_char,
    catalog_index_json: *const c_char,
    ctx_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let entries =
            json_array_or_empty(&unsafe { parse_json_cstr(entries_json, "entries_json")? });
        let build = unsafe { parse_json_cstr(build_catalog_json, "build_catalog_json")? };
        let index = catalog_index_from_json(&unsafe {
            parse_json_cstr(catalog_index_json, "catalog_index_json")?
        });
        let result = policies::append_description_reinstate_entries(&ctx, &entries, &build, &index);
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

bool_ctx_fn!(cyt_needs_description_reinstate, needs_description_reinstate);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_description_policy(policy: *const c_char) -> c_int {
    match unsafe { c_str_to_str(policy, "policy") } {
        Ok(s) => {
            let Some(p) = parse_tool_policy(s) else {
                return 0;
            };
            i32::from(policies::is_description_policy(p))
        }
        Err(code) => code,
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_scoring_policy(policy: *const c_char, out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let s = unsafe { c_str_to_str(policy, "policy")? };
        let p = parse_tool_policy(s).ok_or_else(|| {
            set_error(&format!("invalid policy: {s}"));
            CYT_ERR_INVALID_ARG
        })?;
        unsafe {
            write_string_result(policies::scoring_policy(p).as_str(), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_drop_recomposed_tools_with_empty_properties(
    tools_json: *const c_char,
    catalog_index_json: *const c_char,
    ctx_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let tools = json_array_or_empty(&unsafe { parse_json_cstr(tools_json, "tools_json")? });
        let index = catalog_index_from_json(&unsafe {
            parse_json_cstr(catalog_index_json, "catalog_index_json")?
        });
        let result = policies::drop_recomposed_tools_with_empty_properties(&ctx, &tools, &index);
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_root_tool_id_from_chunk(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        unsafe {
            write_string_result(&policies::root_tool_id_from_chunk(&item), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_chunk_tool_id(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        unsafe {
            write_string_result(&policies::chunk_tool_id(&item), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_non_system_tool_id(tool_id: *const c_char) -> c_int {
    match unsafe { c_str_to_str(tool_id, "tool_id") } {
        Ok(s) => {
            clear_error();
            i32::from(policies::is_non_system_tool_id(s))
        }
        Err(code) => code,
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_system_tool_id(tool_id: *const c_char) -> c_int {
    match unsafe { c_str_to_str(tool_id, "tool_id") } {
        Ok(s) => {
            clear_error();
            i32::from(policies::is_system_tool_id(s))
        }
        Err(code) => code,
    }
}

bool_item_fn!(cyt_is_system_chunk, policies::is_system_chunk);
bool_item_fn!(cyt_is_non_system_chunk, policies::is_non_system_chunk);
bool_item_fn!(cyt_is_system_root_chunk, policies::is_system_root_chunk);
bool_item_fn!(cyt_is_mcp_root_chunk, policies::is_mcp_root_chunk);
bool_item_fn!(
    cyt_is_system_optional_chunk,
    policies::is_system_optional_chunk
);
bool_item_fn!(cyt_is_mcp_optional_chunk, policies::is_mcp_optional_chunk);
bool_item_fn!(
    cyt_is_direct_root_optional_property_chunk,
    policies::is_direct_root_optional_property_chunk
);
bool_item_fn!(
    cyt_root_chunk_properties_empty,
    policies::root_chunk_properties_empty
);

json_array_fn!(cyt_stash_system_tools, stash_system_tools);
json_array_fn!(cyt_restore_system_tools, restore_system_tools);
json_array_fn!(cyt_stash_mcp_tools, stash_mcp_tools);
json_array_fn!(cyt_restore_mcp_tools, restore_mcp_tools);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_merge_tools_preserving_order(
    original_json: *const c_char,
    pruned_by_name_json: *const c_char,
    stashed_by_name_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let original =
            json_array_or_empty(&unsafe { parse_json_cstr(original_json, "original_json")? });
        let pruned: HashMap<String, Value> = json_object_or_empty(&unsafe {
            parse_json_cstr(pruned_by_name_json, "pruned_by_name_json")?
        })
        .into_iter()
        .collect();
        let stashed: HashMap<String, Value> = json_object_or_empty(&unsafe {
            parse_json_cstr(stashed_by_name_json, "stashed_by_name_json")?
        })
        .into_iter()
        .collect();
        let result = policies::merge_tools_preserving_order(&original, &pruned, &stashed);
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_split_anthropic_tools(
    tools_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let arr = json_array_or_empty(&unsafe { parse_json_cstr(tools_json, "tools_json")? });
        let (non_system, system) = policies::split_anthropic_tools(&arr);
        unsafe {
            write_json_out(&json!({ "non_system": non_system, "system": system }), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_entries_for_policy(
    ctx_json: *const c_char,
    all_entries_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let arr =
            json_array_or_empty(&unsafe { parse_json_cstr(all_entries_json, "all_entries_json")? });
        unsafe {
            write_json_out(&Value::Array(policies::entries_for_policy(&ctx, &arr)), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tools_for_catalog(
    ctx_json: *const c_char,
    tools_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let arr = json_array_or_empty(&unsafe { parse_json_cstr(tools_json, "tools_json")? });
        unsafe {
            write_json_out(&Value::Array(policies::tools_for_catalog(&ctx, &arr)), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_system_required_enum_values(
    data_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        unsafe {
            write_json_out(
                &hashset_to_json(policies::system_required_enum_values(&data)),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_mcp_required_enum_values(
    data_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        unsafe {
            write_json_out(
                &hashset_to_json(policies::mcp_required_enum_values(&data)),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_required_enum_values_by_tool(
    data_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let data = unsafe { parse_json_cstr(data_json, "data_json")? };
        let map = policies::required_enum_values_by_tool(&data);
        let obj: Map<String, Value> = map
            .into_iter()
            .map(|(k, v)| (k, hashset_to_json(v)))
            .collect();
        unsafe { write_json_out(&Value::Object(obj), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_optional_leaf_survived_rerank(
    item_json: *const c_char,
    ctx_json: *const c_char,
    rerank_score: f64,
    use_default_rerank_score: c_int,
    llm_selected_paths_json: *const c_char,
    out: *mut c_int,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let ctx = parse_ctx_json(ctx_json)?;
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        let score = if use_default_rerank_score != 0 {
            runtime_config::rerank_score()
        } else {
            rerank_score
        };
        let paths = if llm_selected_paths_json.is_null() {
            None
        } else {
            let val =
                unsafe { parse_json_cstr(llm_selected_paths_json, "llm_selected_paths_json")? };
            optional_string_set_from_json(&val)
        };
        unsafe {
            *out = i32::from(policies::optional_leaf_survived_rerank(
                &ctx,
                &item,
                score,
                paths.as_ref(),
            ));
        }
        clear_error();
        Ok(())
    })
}

bool_ctx_fn!(cyt_needs_partition, needs_partition);
bool_ctx_fn!(cyt_needs_pruned_recompose, needs_pruned_recompose);
bool_ctx_fn!(cyt_system_tools_pass_through, system_tools_pass_through);
bool_ctx_fn!(cyt_mcp_tools_pass_through, mcp_tools_pass_through);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_anthropic_tool_is_system(tool_json: *const c_char) -> c_int {
    ffi_guard(|| {
        let tool = unsafe { parse_json_cstr(tool_json, "tool_json")? };
        clear_error();
        Ok(i32::from(policies::anthropic_tool_is_system(&tool)))
    })
    .unwrap_or_else(|code| code)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_anthropic_tool_is_mcp(tool_json: *const c_char) -> c_int {
    ffi_guard(|| {
        let tool = unsafe { parse_json_cstr(tool_json, "tool_json")? };
        clear_error();
        Ok(i32::from(policies::anthropic_tool_is_mcp(&tool)))
    })
    .unwrap_or_else(|code| code)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_direct_root_optional_chunks_for_tool(
    items_json: *const c_char,
    tool_id: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let arr = json_array_or_empty(&unsafe { parse_json_cstr(items_json, "items_json")? });
        let tool = unsafe { c_str_to_str(tool_id, "tool_id")? };
        let result = policies::direct_root_optional_chunks_for_tool(&arr, tool);
        unsafe { write_json_out(&Value::Array(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tool_id_has_empty_decomposed_root(
    catalog_index_json: *const c_char,
    tool_id: *const c_char,
    out: *mut c_int,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let index = catalog_index_from_json(&unsafe {
            parse_json_cstr(catalog_index_json, "catalog_index_json")?
        });
        let tool = unsafe { c_str_to_str(tool_id, "tool_id")? };
        unsafe {
            *out = i32::from(policies::tool_id_has_empty_decomposed_root(&index, tool));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tool_id_had_empty_original_root_properties(
    catalog_index_json: *const c_char,
    tool_id: *const c_char,
    out: *mut c_int,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let index = catalog_index_from_json(&unsafe {
            parse_json_cstr(catalog_index_json, "catalog_index_json")?
        });
        let tool = unsafe { c_str_to_str(tool_id, "tool_id")? };
        unsafe {
            *out = i32::from(policies::tool_id_had_empty_original_root_properties(
                &index, tool,
            ));
        }
        clear_error();
        Ok(())
    })
}

/// Classify optional chunks for many catalog items in one pass.
///
/// Returns JSON `{"system":[bool,...],"mcp":[bool,...]}`.
///
/// # Safety
///
/// `items_json` must be a JSON array; `out` must be non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_classify_optional_chunks_batch(
    items_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(items_json, "items_json")? };
        let arr = json_array_or_empty(&val);
        let (system, mcp) = policies::classify_optional_chunks_batch(&arr);
        unsafe {
            write_json_out(
                &json!({
                    "system": system,
                    "mcp": mcp,
                }),
                out,
            )?;
        };
        Ok(())
    })
}
