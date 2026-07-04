#![cfg(feature = "ffi")]

use cyt_indexer::bindings::manifest::EXPORTS;
use cyt_indexer::ffi::{
    CYT_OK, cyt_build_skill_node_catalog, cyt_classify_and_count_catalog, cyt_free_string,
};
use std::ffi::CStr;
use std::fs;
use std::os::raw::c_char;
use std::path::PathBuf;
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

fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../e2e/fixtures")
        .join(name)
}

#[test]
fn classify_and_count_catalog_from_fixture() {
    let catalog = fs::read_to_string(fixture_path("bm25_catalog.json")).unwrap_or_else(|_| {
        "{\"json\":[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\",\"content\":\"Read files\"}],\"md\":[]}".to_string()
    });
    let catalog_c = format!("{catalog}\0");
    let mut out: *mut c_char = ptr::null_mut();
    let code = unsafe {
        cyt_classify_and_count_catalog(
            CStr::from_bytes_with_nul_unchecked(catalog_c.as_bytes()).as_ptr(),
            ptr::null(),
            ptr::addr_of_mut!(out),
        )
    };
    assert_eq!(code, CYT_OK);
    let json = unsafe { read_out(out) };
    assert!(json.contains("optional_chunk_count"), "got {json}");
}

#[test]
fn build_skill_node_catalog_empty_entries() {
    let entries = cstr(b"[]\0");
    let mut out: *mut c_char = ptr::null_mut();
    let code = unsafe { cyt_build_skill_node_catalog(entries.as_ptr(), ptr::addr_of_mut!(out)) };
    assert_eq!(code, CYT_OK);
    let json = unsafe { read_out(out) };
    assert_eq!(json, "[]");
}

#[test]
fn pipeline_exports_in_manifest() {
    for name in [
        "cyt_prune_catalog_bm25_and_retrieve",
        "cyt_classify_and_count_catalog",
        "cyt_search_skills_and_select",
        "cyt_build_skill_node_catalog",
    ] {
        assert!(
            EXPORTS.iter().any(|e| e.name == name),
            "pipeline export missing from manifest: {name}"
        );
    }
}
