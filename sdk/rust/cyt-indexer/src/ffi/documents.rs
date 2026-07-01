//! Document extraction FFI exports.

use crate::documents::{
    extract_document_text, extract_json_catalog_document, extract_level_info,
    extract_md_catalog_document,
};
use crate::ffi::error::{CYT_ERR_NULL_PTR, set_error};
use crate::ffi::json_util::{parse_json_cstr, run_ffi, write_json_out, write_optional_string_out};
use serde_json::Value;
use std::os::raw::{c_char, c_int};

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_extract_document_text(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        unsafe { write_optional_string_out(extract_document_text(&item), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_extract_level_info(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        let lines: Vec<Value> = extract_level_info(&item)
            .into_iter()
            .map(Value::String)
            .collect();
        unsafe { write_json_out(&Value::Array(lines), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_extract_json_catalog_document(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        unsafe { write_optional_string_out(extract_json_catalog_document(&item), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_extract_md_catalog_document(
    item_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let item = unsafe { parse_json_cstr(item_json, "item_json")? };
        unsafe { write_optional_string_out(extract_md_catalog_document(&item), out)? };
        Ok(())
    })
}
