//! Memory management for C FFI.

use crate::ffi::error::{CYT_ERR_ALLOC, CYT_ERR_NULL_PTR, CYT_OK, clear_error, set_error};
use std::ffi::CString;
use std::os::raw::{c_char, c_int};
use std::ptr;

/// Free a string allocated by `cyt_*` functions. NULL is safe.
///
/// # Safety
///
/// `s` must be null or a pointer previously returned by a `cyt_*` out-parameter.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_free_string(s: *mut c_char) {
    if !s.is_null() {
        let _ = CString::from_raw(s);
    }
}

pub unsafe fn write_string_out(s: &str, out: *mut *mut c_char) -> c_int {
    match CString::new(s) {
        Ok(cstr) => {
            *out = cstr.into_raw();
            clear_error();
            CYT_OK
        }
        Err(e) => {
            set_error(&format!("string allocation failed: {e}"));
            *out = ptr::null_mut();
            CYT_ERR_ALLOC
        }
    }
}

/// Return the library version string (caller must free with `cyt_free_string`).
///
/// # Safety
///
/// `out` must be a valid pointer to a `char*` that receives an allocated string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_version(out: *mut *mut c_char) -> c_int {
    if out.is_null() {
        set_error("null pointer: out");
        return CYT_ERR_NULL_PTR;
    }
    unsafe { write_string_out(env!("CARGO_PKG_VERSION"), out) }
}
