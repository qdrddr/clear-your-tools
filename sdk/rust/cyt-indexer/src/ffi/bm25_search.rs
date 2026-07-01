//! Tantivy BM25 catalog search FFI exports.

use crate::bm25_search::{
    self, ScoreCatalogOptions, bm25_frontmatter_gate, bm25_search_skill_chunks,
    collect_catalog_documents, score_catalog_in_place,
};
use crate::ffi::error::{CYT_ERR_INVALID_ARG, CYT_ERR_NULL_PTR, set_error};
use crate::ffi::json_util::{
    c_str_to_str, json_array_or_empty, parse_json_cstr, run_ffi, write_json_out,
};
use serde_json::{Value, json};
use std::collections::HashSet;
use std::os::raw::{c_char, c_int};

fn parse_score_options(val: &Value) -> ScoreCatalogOptions {
    let mut options = ScoreCatalogOptions::default();
    if let Some(v) = val.get("prune_json_threshold") {
        options.prune_json_threshold = v.as_f64();
    }
    if let Some(v) = val.get("prune_md_threshold") {
        options.prune_md_threshold = v.as_f64();
    }
    if let Some(v) = val.get("prune_enums").and_then(Value::as_bool) {
        options.prune_enums = v;
    }
    options
}

fn parse_excluded_set(val: &Value) -> HashSet<(String, String)> {
    let mut excluded = HashSet::new();
    for item in json_array_or_empty(val) {
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
            excluded.insert((entry_dir, doc_id));
        }
    }
    excluded
}

/// Override BM25 search defaults. `config_json` may be null or partial JSON.
///
/// # Safety
///
/// When non-null, `config_json` must be a valid null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_configure_bm25_defaults(config_json: *const c_char) -> c_int {
    run_ffi(|| {
        let mut cfg = bm25_search::snapshot();
        if !config_json.is_null() {
            let val = unsafe { parse_json_cstr(config_json, "config_json")? };
            if let Some(dir) = val.get("index_dir").and_then(Value::as_str) {
                cfg.index_dir = dir.into();
            }
            if let Some(v) = val.get("stem_language").and_then(Value::as_str) {
                cfg.stem_language = v.to_string();
            }
            if let Some(v) = val.get("stopwords").and_then(Value::as_str) {
                cfg.stopwords = v.to_string();
            }
            if let Some(v) = val.get("use_stopwords").and_then(Value::as_bool) {
                cfg.use_stopwords = v;
            }
            if let Some(v) = val.get("k1").and_then(Value::as_f64) {
                cfg.k1 = v;
            }
            if let Some(v) = val.get("b").and_then(Value::as_f64) {
                cfg.b = v;
            }
            if let Some(v) = val.get("mmap").and_then(Value::as_bool) {
                cfg.mmap = v;
            }
        }
        bm25_search::configure(&cfg);
        Ok(())
    })
}

/// Hash catalog documents plus analyzer settings.
///
/// # Safety
///
/// `data_json` and `out` must be valid pointers. `out` receives an allocated string
/// that the caller must free with [`cyt_free_string`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_catalog_fingerprint(
    data_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(data_json, "data_json")? };
        let cfg = bm25_search::snapshot();
        let docs = collect_catalog_documents(&val);
        let fingerprint =
            bm25_search::catalog_fingerprint(&docs, &cfg.stem_language, &cfg.stopwords);
        unsafe { crate::ffi::json_util::write_string_result(&fingerprint, out)? };
        Ok(())
    })
}

/// Score catalog json/md lists in-place and return the updated catalog JSON.
///
/// # Safety
///
/// `data_json`, `query`, and `out` must be valid pointers. `options_json` may be null.
/// `out` receives an allocated JSON string that the caller must free with [`cyt_free_string`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_score_catalog(
    data_json: *const c_char,
    query: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let mut val = unsafe { parse_json_cstr(data_json, "data_json")? };
        let q = unsafe { c_str_to_str(query, "query")? };
        let options = if options_json.is_null() {
            ScoreCatalogOptions::default()
        } else {
            let opt_val = unsafe { parse_json_cstr(options_json, "options_json")? };
            parse_score_options(&opt_val)
        };
        score_catalog_in_place(&mut val, q, &options).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&val, out)? };
        Ok(())
    })
}

/// Return excluded entry refs and trace metadata for frontmatter gating.
///
/// # Safety
///
/// `entries_json`, `query`, and `out` must be valid pointers. `out` receives an allocated
/// JSON string that the caller must free with [`cyt_free_string`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_frontmatter_gate(
    entries_json: *const c_char,
    query: *const c_char,
    upper_limit: f64,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let entries_val = unsafe { parse_json_cstr(entries_json, "entries_json")? };
        let q = unsafe { c_str_to_str(query, "query")? };
        let arr = json_array_or_empty(&entries_val);
        let (excluded, trace) = bm25_frontmatter_gate(&arr, q, upper_limit).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        let excluded_json: Vec<Value> = excluded
            .into_iter()
            .map(|(entry_dir, doc_id)| json!({ "entry_dir": entry_dir, "doc_id": doc_id }))
            .collect();
        let result = json!({ "excluded": excluded_json, "trace": trace });
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}

/// Search skill chunks, reconstruct matches, return matches + trace JSON.
///
/// # Safety
///
/// `entries_json`, `query`, and `out` must be valid pointers. `excluded_json` may be null.
/// `out` receives an allocated JSON string that the caller must free with [`cyt_free_string`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_search_skill_chunks(
    entries_json: *const c_char,
    query: *const c_char,
    threshold: f64,
    excluded_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let entries_val = unsafe { parse_json_cstr(entries_json, "entries_json")? };
        let q = unsafe { c_str_to_str(query, "query")? };
        let arr = json_array_or_empty(&entries_val);
        let excluded = if excluded_json.is_null() {
            HashSet::new()
        } else {
            let ex_val = unsafe { parse_json_cstr(excluded_json, "excluded_json")? };
            parse_excluded_set(&ex_val)
        };
        let result = bm25_search_skill_chunks(&arr, q, threshold, &excluded).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}
