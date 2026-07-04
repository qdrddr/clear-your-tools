//! Composite pipeline FFI exports.

use crate::build::catalog_index_from_value;
use crate::ffi::error::{CYT_ERR_INVALID_ARG, CYT_ERR_NULL_PTR, set_error};
use crate::ffi::json_util::{
    c_str_to_str, json_array_or_empty, parse_json_cstr, parse_policy_context, run_ffi,
    write_json_out,
};
use crate::pipeline::{
    PruneBm25Options, SearchSkillsOptions, build_skill_node_catalog, classify_and_count_catalog,
    prune_catalog_bm25_and_retrieve,
};
use crate::policies::PolicyContext;
use serde_json::{Map, Value, json};
use std::os::raw::{c_char, c_int};

fn parse_ctx_json(ctx_json: *const c_char) -> Result<PolicyContext, c_int> {
    if ctx_json.is_null() {
        return Ok(parse_policy_context(&Value::Object(Map::new())));
    }
    let val = unsafe { parse_json_cstr(ctx_json, "ctx_json")? };
    Ok(parse_policy_context(&val))
}

fn prune_options_from_json(val: &Value) -> PruneBm25Options {
    let mut opts = PruneBm25Options::default();
    if let Some(v) = val.get("score_tool").and_then(Value::as_f64) {
        opts.score_tool = v;
    }
    if let Some(v) = val.get("score_tool_enum").and_then(Value::as_f64) {
        opts.score_tool_enum = v;
    }
    if let Some(v) = val.get("prune_enums").and_then(Value::as_bool) {
        opts.prune_enums = v;
    }
    if let Some(arr) = val.get("pipeline").and_then(Value::as_array) {
        opts.pipeline = arr
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
    }
    opts
}

fn search_options_from_json(val: &Value) -> SearchSkillsOptions {
    let mut opts = SearchSkillsOptions::default();
    if let Some(v) = val.get("threshold").and_then(Value::as_f64) {
        opts.threshold = v;
    }
    if let Some(v) = val.get("max_tokens").and_then(Value::as_i64) {
        opts.max_tokens = v;
    }
    if let Some(v) = val.get("frontmatter_upper_limit").and_then(Value::as_f64) {
        opts.frontmatter_upper_limit = Some(v);
    }
    if let Some(v) = val.get("item_kind").and_then(Value::as_str) {
        opts.item_kind = v.to_string();
    }
    opts
}

fn prune_result_to_json(result: crate::pipeline::PruneRetrieveResult) -> Value {
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
    json!({
        "tools": result.tools,
        "decomposed": decomposed,
        "decomposed_breakdown": breakdown,
        "optional_chunk_count_in": result.optional_chunk_count_in,
        "optional_chunk_count_out": result.optional_chunk_count_out,
    })
}

#[unsafe(no_mangle)]
/// Prune a tool catalog with BM25 scoring and retrieve upstream tools.
///
/// # Safety
///
/// All JSON pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
pub unsafe extern "C" fn cyt_prune_catalog_bm25_and_retrieve(
    catalog_json: *const c_char,
    build_catalog_json: *const c_char,
    catalog_index_json: *const c_char,
    query: *const c_char,
    scoring_ctx_json: *const c_char,
    output_ctx_json: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let catalog = unsafe { parse_json_cstr(catalog_json, "catalog_json")? };
        let build_catalog = unsafe { parse_json_cstr(build_catalog_json, "build_catalog_json")? };
        let index_val = unsafe { parse_json_cstr(catalog_index_json, "catalog_index_json")? };
        let q = unsafe { c_str_to_str(query, "query")? };
        let scoring = parse_ctx_json(scoring_ctx_json)?;
        let output = parse_ctx_json(output_ctx_json)?;
        let opts = if options_json.is_null() {
            PruneBm25Options::default()
        } else {
            prune_options_from_json(&unsafe { parse_json_cstr(options_json, "options_json")? })
        };
        let index = catalog_index_from_value(&index_val);
        let result = prune_catalog_bm25_and_retrieve(
            &catalog,
            &build_catalog,
            &index,
            q,
            &scoring,
            &output,
            &opts,
        )
        .map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&prune_result_to_json(result), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
/// Classify optional catalog chunks and optionally count tool JSON tokens.
///
/// # Safety
///
/// `catalog_json` must be a valid null-terminated UTF-8 C string; `tools_json` may be null;
/// `out` must be non-null.
pub unsafe extern "C" fn cyt_classify_and_count_catalog(
    catalog_json: *const c_char,
    tools_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let catalog = unsafe { parse_json_cstr(catalog_json, "catalog_json")? };
        let tools_val = if tools_json.is_null() {
            None
        } else {
            Some(json_array_or_empty(&unsafe {
                parse_json_cstr(tools_json, "tools_json")?
            }))
        };
        let tools_slice = tools_val.as_deref();
        let result = classify_and_count_catalog(&catalog, tools_slice).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
/// BM25 skill search with optional frontmatter gate and greedy budget selection.
///
/// # Safety
///
/// `entries_json`, `query`, and `options_json` must be valid null-terminated UTF-8 C strings
/// (`options_json` may be null); `out` must be non-null.
pub unsafe extern "C" fn cyt_search_skills_and_select(
    entries_json: *const c_char,
    query: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let entries =
            json_array_or_empty(&unsafe { parse_json_cstr(entries_json, "entries_json")? });
        let q = unsafe { c_str_to_str(query, "query")? };
        let opts = if options_json.is_null() {
            SearchSkillsOptions::default()
        } else {
            search_options_from_json(&unsafe { parse_json_cstr(options_json, "options_json")? })
        };
        let result =
            crate::pipeline::search_skills_and_select(&entries, q, &opts).map_err(|e| {
                set_error(&e);
                CYT_ERR_INVALID_ARG
            })?;
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
/// Build rerankable node bodies from cached skill entries.
///
/// # Safety
///
/// `entries_json` must be a valid null-terminated UTF-8 C string; `out` must be non-null.
pub unsafe extern "C" fn cyt_build_skill_node_catalog(
    entries_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let entries =
            json_array_or_empty(&unsafe { parse_json_cstr(entries_json, "entries_json")? });
        let items = build_skill_node_catalog(&entries).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&Value::Array(items), out)? };
        Ok(())
    })
}
