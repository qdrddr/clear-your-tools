//! Catalog build and tool-entry FFI exports (mirrors `python.rs`).

use crate::build::{build_catalog_index, catalog_tool_count};
use crate::ffi::error::{CYT_ERR_INVALID_ARG, CYT_ERR_NULL_PTR, clear_error, set_error};
use crate::ffi::json_util::{
    c_str_to_str, catalog_index_from_json, ffi_guard, json_array_or_empty, parse_json_cstr,
    run_ffi, write_json_out, write_string_result,
};
use crate::tool_entries::{
    anthropic_tool_to_catalog_entry, anthropic_tools_to_catalog_entries, build_catalog_from_tools,
    prepare_tool_entry, truncate_description,
};
use serde_json::json;
use std::os::raw::{c_char, c_int, c_long, c_ulong};

/// Count tools in a catalog dict JSON.
///
/// # Safety
///
/// `data_json` must be a valid null-terminated UTF-8 C string, or null (returns -1).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_catalog_tool_count(data_json: *const c_char) -> c_long {
    ffi_guard(|| {
        let val = unsafe { parse_json_cstr(data_json, "data_json")? };
        clear_error();
        i64::try_from(catalog_tool_count(&val))
            .map_err(|_| {
                set_error("catalog tool count overflow");
                CYT_ERR_INVALID_ARG
            })
            .map(|count| count as c_long)
    })
    .unwrap_or(-1)
}

/// Build a catalog index from tools and enums JSON arrays.
///
/// # Safety
///
/// `tools_json`, `enums_json`, and `out` must be valid pointers. `out` receives an
/// allocated JSON string that the caller must free with [`cyt_free_string`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_build_catalog_index(
    tools_json: *const c_char,
    enums_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let tools_val = unsafe { parse_json_cstr(tools_json, "tools_json")? };
        let enums_val = unsafe { parse_json_cstr(enums_json, "enums_json")? };
        let tools = json_array_or_empty(&tools_val);
        let enums = json_array_or_empty(&enums_val);
        let index = build_catalog_index(&tools, &enums);
        let result = json!({ "tools": index.tools, "files": index.files });
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}

/// Convert Anthropic tools to catalog entries and enums.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_anthropic_tools_to_catalog_entries(
    tools_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let tools_val = unsafe { parse_json_cstr(tools_json, "tools_json")? };
        let tools = json_array_or_empty(&tools_val);
        let (entries, enums) = anthropic_tools_to_catalog_entries(&tools);
        unsafe {
            write_json_out(&json!({ "entries": entries, "enums": enums }), out)?;
        }
        Ok(())
    })
}

/// Build catalog index from normalized tool entries.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_build_catalog_from_tools(
    tools_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let tools_val = unsafe { parse_json_cstr(tools_json, "tools_json")? };
        let tools = json_array_or_empty(&tools_val);
        let index = build_catalog_from_tools(&tools);
        unsafe {
            write_json_out(&json!({ "tools": index.tools, "files": index.files }), out)?;
        }
        Ok(())
    })
}

/// Prepare a single tool catalog entry.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_prepare_tool_entry(
    server_name: *const c_char,
    name: *const c_char,
    description: *const c_char,
    input_schema_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let server = unsafe { c_str_to_str(server_name, "server_name")? };
        let tool_name = unsafe { c_str_to_str(name, "name")? };
        let desc = unsafe { c_str_to_str(description, "description")? };
        let schema = unsafe { parse_json_cstr(input_schema_json, "input_schema_json")? };
        let entry = prepare_tool_entry(server, tool_name, desc, &schema);
        unsafe { write_json_out(&entry, out)? };
        Ok(())
    })
}

/// Convert one Anthropic tool to a catalog entry. Writes null to `out` when none.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_anthropic_tool_to_catalog_entry(
    tool_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let tool = unsafe { parse_json_cstr(tool_json, "tool_json")? };
        if let Some(entry) = anthropic_tool_to_catalog_entry(&tool) {
            unsafe { write_json_out(&entry, out)? };
        } else {
            unsafe { *out = std::ptr::null_mut() };
            clear_error();
        }
        Ok(())
    })
}

/// Truncate a tool description to a token budget.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_truncate_description(
    description: *const c_char,
    max_tokens: c_ulong,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let desc = unsafe { c_str_to_str(description, "description")? };
        let max = usize::try_from(max_tokens).map_err(|_| {
            set_error("max_tokens exceeds platform limits");
            CYT_ERR_INVALID_ARG
        })?;
        let truncated = truncate_description(desc, max);
        unsafe { write_string_result(&truncated, out)? };
        Ok(())
    })
}

/// Convert catalog index JSON to catalog dict for retrieval.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_catalog_index_to_catalog_dict(
    index_json: *const c_char,
    catalog_prefix: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_json, "index_json")? };
        let idx = catalog_index_from_json(&val);
        let result = if catalog_prefix.is_null() {
            idx.to_catalog_dict()
        } else {
            let prefix = unsafe { c_str_to_str(catalog_prefix, "catalog_prefix")? };
            idx.to_catalog_dict_with_prefix(prefix)
        };
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}

/// Return cached full/decomposed tool schema token metadata from catalog index JSON.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_catalog_index_tool_schema_metadata(
    index_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_json, "index_json")? };
        let idx = catalog_index_from_json(&val);
        let result = idx.tool_schema_metadata();
        unsafe { write_json_out(&result, out)? };
        Ok(())
    })
}
