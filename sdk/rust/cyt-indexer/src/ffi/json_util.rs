//! JSON parsing, policy context, and FFI panic guards.

use crate::build::{CatalogIndex, catalog_index_from_value};
use crate::ffi::error::{
    CYT_ERR_INVALID_UTF8, CYT_ERR_JSON, CYT_ERR_PANIC, CYT_OK, clear_error, set_error,
};
use crate::ffi::memory::write_string_out;
use crate::policies::{PolicyContext, parse_tool_policy, policy_context_from_values};
use crate::runtime_config;
use serde_json::Value;
use std::collections::HashMap;
use std::ffi::CStr;
use std::os::raw::{c_char, c_int};
use std::panic::catch_unwind;
use std::ptr;

pub unsafe fn c_str_to_str<'a>(ptr: *const c_char, name: &str) -> Result<&'a str, c_int> {
    if ptr.is_null() {
        set_error(&format!("null pointer: {name}"));
        return Err(crate::ffi::error::CYT_ERR_NULL_PTR);
    }
    match CStr::from_ptr(ptr).to_str() {
        Ok(s) => Ok(s),
        Err(e) => {
            set_error(&format!("invalid UTF-8 in {name}: {e}"));
            Err(CYT_ERR_INVALID_UTF8)
        }
    }
}

pub unsafe fn parse_json_cstr(ptr: *const c_char, name: &str) -> Result<Value, c_int> {
    let s = c_str_to_str(ptr, name)?;
    serde_json::from_str(s).map_err(|e| {
        set_error(&format!("JSON parse error in {name}: {e}"));
        CYT_ERR_JSON
    })
}

pub unsafe fn write_json_out(value: &Value, out: *mut *mut c_char) -> Result<(), c_int> {
    if out.is_null() {
        set_error("null pointer: out");
        return Err(crate::ffi::error::CYT_ERR_NULL_PTR);
    }
    match serde_json::to_string(value) {
        Ok(s) => {
            let code = write_string_out(s.as_str(), out);
            if code == CYT_OK { Ok(()) } else { Err(code) }
        }
        Err(e) => {
            set_error(&format!("JSON serialize error: {e}"));
            *out = ptr::null_mut();
            Err(CYT_ERR_JSON)
        }
    }
}

pub unsafe fn write_optional_string_out(
    value: Option<String>,
    out: *mut *mut c_char,
) -> Result<(), c_int> {
    if let Some(s) = value {
        let code = write_string_out(&s, out);
        if code == CYT_OK { Ok(()) } else { Err(code) }
    } else {
        *out = ptr::null_mut();
        clear_error();
        Ok(())
    }
}

pub unsafe fn write_string_result(s: &str, out: *mut *mut c_char) -> Result<(), c_int> {
    let code = write_string_out(s, out);
    if code == CYT_OK { Ok(()) } else { Err(code) }
}

pub fn run_ffi<F>(f: F) -> c_int
where
    F: FnOnce() -> Result<(), c_int> + std::panic::UnwindSafe,
{
    match ffi_guard(f) {
        Ok(()) => CYT_OK,
        Err(code) => code,
    }
}

pub fn json_array_or_empty(val: &Value) -> Vec<Value> {
    val.as_array().cloned().unwrap_or_default()
}

pub fn json_object_or_empty(val: &Value) -> serde_json::Map<String, Value> {
    val.as_object().cloned().unwrap_or_default()
}

/// Parse policy context from JSON supporting both `policy_context_from_values` config
/// and direct `{system_policy, mcp_policy, per_tool, tool_kind}` objects.
///
/// `tool_kind` (`"system"` | `"mcp"`) is an optional runtime batch override: when set,
/// all tools in the session use MCP or system classification instead of the `mcp__` prefix.
pub fn parse_policy_context(val: &Value) -> PolicyContext {
    if val.get("system_policy").is_some()
        || val.get("mcp_policy").is_some()
        || val.get("per_tool").is_some()
        || val.get("tool_kind").is_some()
    {
        let system_policy = val
            .get("system_policy")
            .and_then(Value::as_str)
            .and_then(parse_tool_policy)
            .or_else(|| parse_tool_policy(&runtime_config::default_system_policy()));
        let mcp_policy = val
            .get("mcp_policy")
            .and_then(Value::as_str)
            .and_then(parse_tool_policy)
            .or_else(|| parse_tool_policy(&runtime_config::default_mcp_policy()));
        let mut per_tool = HashMap::new();
        if let Some(map) = val.get("per_tool").and_then(Value::as_object) {
            for (k, v) in map {
                if let Some(p) = v.as_str().and_then(parse_tool_policy) {
                    per_tool.insert(k.clone(), p);
                }
            }
        }
        let mut ctx = PolicyContext::with_overrides(system_policy, mcp_policy, per_tool);
        if let Some(kind) = val
            .get("tool_kind")
            .and_then(Value::as_str)
            .and_then(|s| s.parse().ok())
        {
            ctx.tool_kind_override = Some(kind);
        }
        return ctx;
    }
    policy_context_from_values(val)
}

pub fn catalog_index_from_json(val: &Value) -> CatalogIndex {
    catalog_index_from_value(val)
}

pub fn optional_string_set_from_json(val: &Value) -> Option<std::collections::HashSet<String>> {
    val.as_array().map(|arr| {
        arr.iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect()
    })
}

pub fn ffi_guard<F, T>(f: F) -> Result<T, c_int>
where
    F: FnOnce() -> Result<T, c_int> + std::panic::UnwindSafe,
{
    catch_unwind(f).unwrap_or_else(|_| {
        set_error("internal panic at FFI boundary");
        Err(CYT_ERR_PANIC)
    })
}
