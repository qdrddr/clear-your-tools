#![cfg(feature = "ffi")]

use cyt_indexer::ffi::{
    CYT_OK, cyt_build_catalog_index, cyt_catalog_tool_count, cyt_count_tokens, cyt_free_string,
};
use std::ffi::CStr;
use std::os::raw::c_char;
use std::ptr;

const fn cstr(bytes: &'static [u8]) -> &'static CStr {
    // SAFETY: every `cstr(...)` literal in this module is nul-terminated.
    unsafe { CStr::from_bytes_with_nul_unchecked(bytes) }
}

unsafe fn read_out(out: *mut c_char) -> String {
    let s = unsafe { CStr::from_ptr(out).to_string_lossy().into_owned() };
    unsafe { cyt_free_string(out) };
    s
}

#[test]
fn catalog_tool_count_smoke() {
    let data = cstr(b"{\"json\":[],\"md\":[]}\0");
    let count = unsafe { cyt_catalog_tool_count(data.as_ptr()) };
    assert_eq!(count, 0);
}

#[test]
fn build_catalog_index_smoke() {
    let tools = cstr(b"[]\0");
    let enums = cstr(b"[]\0");
    let mut out: *mut c_char = ptr::null_mut();
    let code =
        unsafe { cyt_build_catalog_index(tools.as_ptr(), enums.as_ptr(), ptr::addr_of_mut!(out)) };
    assert_eq!(code, CYT_OK);
    assert!(!out.is_null());
    let json = unsafe { read_out(out) };
    assert!(json.contains("\"tools\""));
    assert!(json.contains("\"files\""));
}

#[test]
fn count_tokens_smoke() {
    let text = cstr(b"hello world\0");
    let count = unsafe { cyt_count_tokens(text.as_ptr()) };
    assert!(count >= 1);
}

#[test]
fn build_catalog_index_with_tool_smoke() {
    let tools = cstr(
        b"[{\"server\":\"s\",\"tool\":\"t\",\"full_schema\":{\"inputSchema\":{\"type\":\"object\",\"properties\":{\"x\":{\"type\":\"string\"}}}}}]\0",
    );
    let enums = cstr(b"[]\0");
    let mut out: *mut c_char = ptr::null_mut();
    let code =
        unsafe { cyt_build_catalog_index(tools.as_ptr(), enums.as_ptr(), ptr::addr_of_mut!(out)) };
    assert_eq!(code, CYT_OK);
    let json = unsafe { read_out(out) };
    assert!(json.contains("\"tools\""));
    assert!(!json.contains("\"tools\":[]"));
}
