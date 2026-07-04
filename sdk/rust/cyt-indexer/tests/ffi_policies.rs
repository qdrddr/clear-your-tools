#![cfg(feature = "ffi")]
#![allow(clippy::panic, clippy::expect_used, clippy::unwrap_used)]

use cyt_indexer::bindings::manifest::{CBINDGEN_STUB_SYMBOLS, EXPORTS};
use cyt_indexer::ffi::{CYT_OK, cyt_classify_optional_chunks_batch, cyt_free_string};
use std::ffi::CStr;
use std::fs;
use std::os::raw::{c_char, c_int};
use std::path::PathBuf;
use std::ptr;

unsafe extern "C" {
    fn cyt_full_pass_through(ctx_json: *const c_char) -> c_int;
    fn cyt_is_non_system_chunk(item_json: *const c_char) -> c_int;
    fn cyt_tool_pass_through(ctx_json: *const c_char, tool_id: *const c_char) -> c_int;
    fn cyt_batch_tool_pass_through(
        ctx_json: *const c_char,
        tool_ids_json: *const c_char,
        out: *mut *mut c_char,
    ) -> c_int;
    fn cyt_stash_system_tools(input_json: *const c_char, out: *mut *mut c_char) -> c_int;
}

const fn cstr(bytes: &'static [u8]) -> &'static CStr {
    // SAFETY: every `cstr(...)` literal in this module is nul-terminated.
    unsafe { CStr::from_bytes_with_nul_unchecked(bytes) }
}

unsafe fn read_out(out: *mut c_char) -> String {
    let s = unsafe { CStr::from_ptr(out).to_string_lossy().into_owned() };
    unsafe { cyt_free_string(out) };
    s
}

fn header_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("cyt_indexer.h")
}

#[test]
fn manifest_symbols_in_generated_header() {
    let header = fs::read_to_string(header_path()).expect("read cyt_indexer.h");
    for exp in EXPORTS {
        assert!(
            header.contains(exp.name),
            "missing symbol in cyt_indexer.h: {}",
            exp.name
        );
    }
    for name in CBINDGEN_STUB_SYMBOLS {
        assert!(
            header.contains(name),
            "missing cbindgen stub symbol in cyt_indexer.h: {name}"
        );
    }
}

struct BoolCase {
    name: &'static str,
    ctx: &'static CStr,
    tool: Option<&'static CStr>,
    run: fn(*const c_char, Option<*const c_char>) -> i32,
    want: bool,
}

fn run_bool_ctx(ctx: *const c_char, _: Option<*const c_char>) -> i32 {
    unsafe { cyt_full_pass_through(ctx) }
}

fn run_tool_pass(ctx: *const c_char, tool: Option<*const c_char>) -> i32 {
    unsafe { cyt_tool_pass_through(ctx, tool.expect("tool required for run_tool_pass")) }
}

#[test]
fn policy_bool_queries_table() {
    let ctx_always =
        cstr(b"{\"system_policy\":\"always_include\",\"mcp_policy\":\"always_include\"}\0");
    let ctx_prune = cstr(b"{\"system_policy\":\"prune_optional\",\"mcp_policy\":\"prune_all\"}\0");
    let agent = cstr(b"Agent\0");
    let grep = cstr(b"grep\0");

    let cases = [
        BoolCase {
            name: "full_pass_through always_include",
            ctx: ctx_always,
            tool: None,
            run: run_bool_ctx,
            want: true,
        },
        BoolCase {
            name: "full_pass_through prune",
            ctx: ctx_prune,
            tool: None,
            run: run_bool_ctx,
            want: false,
        },
        BoolCase {
            name: "tool_pass_through Agent",
            ctx: ctx_always,
            tool: Some(agent),
            run: run_tool_pass,
            want: true,
        },
        BoolCase {
            name: "tool_pass_through grep",
            ctx: ctx_prune,
            tool: Some(grep),
            run: run_tool_pass,
            want: false,
        },
    ];

    for case in cases {
        let code = (case.run)(case.ctx.as_ptr(), case.tool.map(CStr::as_ptr));
        assert_eq!(
            code,
            i32::from(case.want),
            "{}: expected {}",
            case.name,
            case.want
        );
    }
}

struct JsonCase {
    name: &'static str,
    ctx: &'static CStr,
    payload: &'static CStr,
    run: fn(*const c_char, *const c_char, *mut *mut c_char) -> i32,
    want_substr: &'static str,
}

fn run_batch_pass(ctx: *const c_char, payload: *const c_char, out: *mut *mut c_char) -> i32 {
    unsafe { cyt_batch_tool_pass_through(ctx, payload, out) }
}

fn run_classify_optional(payload: *const c_char, _: *const c_char, out: *mut *mut c_char) -> i32 {
    unsafe { cyt_classify_optional_chunks_batch(payload, out) }
}

fn run_stash_system(payload: *const c_char, _: *const c_char, out: *mut *mut c_char) -> i32 {
    unsafe { cyt_stash_system_tools(payload, out) }
}

#[test]
fn policy_json_exports_table() {
    let tool_ids = cstr(b"[\"Agent\",\"grep\"]\0");
    let items = cstr(b"[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\"}]\0");
    let tools = cstr(b"[{\"name\":\"Agent\",\"type\":\"system\"}]\0");
    let empty = cstr(b"{}\0");

    let cases = [
        JsonCase {
            name: "batch_tool_pass_through",
            ctx: cstr(
                b"{\"system_policy\":\"always_include\",\"mcp_policy\":\"always_include\"}\0",
            ),
            payload: tool_ids,
            run: run_batch_pass,
            want_substr: "true",
        },
        JsonCase {
            name: "classify_optional_chunks_batch",
            ctx: empty,
            payload: items,
            run: run_classify_optional,
            want_substr: "\"system\"",
        },
        JsonCase {
            name: "stash_system_tools",
            ctx: empty,
            payload: tools,
            run: run_stash_system,
            want_substr: "[",
        },
    ];

    for case in cases {
        let mut out: *mut c_char = ptr::null_mut();
        let code = (case.run)(
            case.ctx.as_ptr(),
            case.payload.as_ptr(),
            ptr::addr_of_mut!(out),
        );
        assert_eq!(code, CYT_OK, "{} returned error", case.name);
        assert!(!out.is_null(), "{} returned null out", case.name);
        let json = unsafe { read_out(out) };
        assert!(
            json.contains(case.want_substr),
            "{}: expected {:?} in {}",
            case.name,
            case.want_substr,
            json
        );
    }
}

#[test]
fn chunk_classifier_macros_smoke() {
    let item = cstr(b"{\"file_path\":\"schemas/decomposed/mcp__test__read.json\"}\0");
    let code = unsafe { cyt_is_non_system_chunk(item.as_ptr()) };
    assert!(code >= 0, "is_non_system_chunk errored");
}
