//! C header stubs for macro-generated FFI symbols.
//!
//! Parsed by cbindgen only (`cbindgen.toml` `[parse] include`); not compiled into the library.
//! Implementations live in `ffi/policies.rs` and `ffi/paths.rs`.

use std::os::raw::{c_char, c_int};

// --- policies: bool_ctx_fn ---

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_full_pass_through(ctx_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_needs_description_reinstate(ctx_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_needs_partition(ctx_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_needs_pruned_recompose(ctx_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_system_tools_pass_through(ctx_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_mcp_tools_pass_through(ctx_json: *const c_char) -> c_int {
    0
}

// --- policies: bool_ctx_tool_fn ---

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tool_pass_through(
    ctx_json: *const c_char,
    tool_id: *const c_char,
) -> c_int {
    0
}

// --- policies: bool_item_fn ---

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_decomposed_tool_root_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_decomposed_optional_property_chunk(
    item_json: *const c_char,
) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_system_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_non_system_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_system_root_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_mcp_root_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_system_optional_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_mcp_optional_chunk(item_json: *const c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_is_direct_root_optional_property_chunk(
    item_json: *const c_char,
) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_root_chunk_properties_empty(item_json: *const c_char) -> c_int {
    0
}

// --- policies: json_array_fn ---

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_stash_system_tools(
    input_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_restore_system_tools(
    input_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_stash_mcp_tools(
    input_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_restore_mcp_tools(
    input_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    0
}

// --- paths: path_getter ---

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_md_ext(out: *mut *mut c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_json_ext(out: *mut *mut c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_decomposed_prefix(out: *mut *mut c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_decomposed_root(out: *mut *mut c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_catalog_prefix(out: *mut *mut c_char) -> c_int {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_default_catalog_dir(out: *mut *mut c_char) -> c_int {
    0
}
