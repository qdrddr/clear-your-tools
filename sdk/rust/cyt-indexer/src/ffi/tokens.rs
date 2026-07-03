//! Tiktoken token counting FFI exports.

use crate::ffi::error::{CYT_ERR_INVALID_ARG, clear_error, set_error};
use crate::ffi::json_util::{c_str_to_str, ffi_guard, parse_json_cstr, run_ffi, write_json_out};
use crate::tiktoken::{self, AllowedSpecial};
use std::os::raw::{c_char, c_int, c_long};

/// Count tokens in UTF-8 text using the configured tiktoken encoding.
///
/// Returns the token count on success, or `-1` on error (`cyt_get_last_error()`).
///
/// # Safety
///
/// `text` must be a valid null-terminated UTF-8 C string, or null (returns -1).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_count_tokens(text: *const c_char) -> c_long {
    ffi_guard(|| {
        let s = unsafe { c_str_to_str(text, "text")? };
        let count = tiktoken::count_tokens(s).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        clear_error();
        i64::try_from(count)
            .map_err(|_| {
                set_error("token count overflow");
                CYT_ERR_INVALID_ARG
            })
            .map(|n| n as c_long)
    })
    .unwrap_or(-1)
}

/// Count tokens for compact JSON text.
///
/// Returns the token count on success, or `-1` on error.
///
/// # Safety
///
/// `json` must be a valid null-terminated UTF-8 C string, or null (returns -1).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_count_json_tokens(json: *const c_char) -> c_long {
    ffi_guard(|| {
        let s = unsafe { c_str_to_str(json, "json")? };
        let value = serde_json::from_str(s).map_err(|e| {
            set_error(&format!("JSON parse error in json: {e}"));
            crate::ffi::error::CYT_ERR_JSON
        })?;
        let count = tiktoken::count_json_tokens(&value).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        clear_error();
        i64::try_from(count)
            .map_err(|_| {
                set_error("token count overflow");
                CYT_ERR_INVALID_ARG
            })
            .map(|n| n as c_long)
    })
    .unwrap_or(-1)
}

/// Override tokenizer defaults. `config_json` may be null or partial JSON:
/// `{"encoding":"cl100k_base","allowed_special":"all"|"none"}`.
///
/// # Safety
///
/// When non-null, `config_json` must be a valid null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_configure_tokenizer_defaults(config_json: *const c_char) -> c_int {
    run_ffi(|| {
        let mut cfg = tiktoken::snapshot();
        if !config_json.is_null() {
            let val = unsafe { parse_json_cstr(config_json, "config_json")? };
            if let Some(enc) = val.get("encoding").and_then(|v| v.as_str()) {
                cfg.encoding = enc.to_string();
            }
            if let Some(mode) = val.get("allowed_special").and_then(|v| v.as_str()) {
                cfg.allowed_special = match mode.to_ascii_lowercase().as_str() {
                    "none" => AllowedSpecial::None,
                    _ => AllowedSpecial::All,
                };
            }
        }
        tiktoken::configure(cfg);
        Ok(())
    })
}

/// Count tokens for multiple UTF-8 strings.
///
/// `texts_json` must be a JSON array of strings. Writes a JSON array of counts to `out`.
///
/// # Safety
///
/// When non-null, `texts_json` must be valid UTF-8 JSON; `out` must be non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_count_tokens_batch(
    texts_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(crate::ffi::error::CYT_ERR_NULL_PTR);
        }
        let val = parse_json_cstr(texts_json, "texts_json")?;
        let arr = val.as_array().cloned().unwrap_or_default();
        let texts: Vec<String> = arr
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
        let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
        let counts = tiktoken::count_tokens_batch(&refs).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        let json_counts: Vec<serde_json::Value> =
            counts.into_iter().map(|n| serde_json::json!(n)).collect();
        unsafe { write_json_out(&serde_json::Value::Array(json_counts), out)? };
        Ok(())
    })
}
