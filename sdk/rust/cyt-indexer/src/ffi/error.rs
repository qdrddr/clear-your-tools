//! Error handling for C FFI (thread-local, mirrors hedl pattern).

use std::cell::RefCell;
use std::ffi::CString;
use std::os::raw::{c_char, c_int};
use std::ptr;

/// Success return code.
pub const CYT_OK: c_int = 0;
/// Null pointer argument error.
pub const CYT_ERR_NULL_PTR: c_int = -1;
/// Invalid UTF-8 encoding error.
pub const CYT_ERR_INVALID_UTF8: c_int = -2;
/// JSON parse or serialization error.
pub const CYT_ERR_JSON: c_int = -3;
/// Memory allocation error.
pub const CYT_ERR_ALLOC: c_int = -4;
/// I/O or filesystem error.
pub const CYT_ERR_IO: c_int = -5;
/// Invalid opaque handle.
pub const CYT_ERR_INVALID_HANDLE: c_int = -6;
/// Internal panic (caught at FFI boundary).
pub const CYT_ERR_PANIC: c_int = -7;
/// Invalid argument / value error.
pub const CYT_ERR_INVALID_ARG: c_int = -8;

thread_local! {
    static LAST_ERROR: RefCell<Option<CString>> = const { RefCell::new(None) };
}

pub fn set_error(msg: &str) {
    LAST_ERROR.with(|e| {
        if let Ok(mut err) = e.try_borrow_mut() {
            *err = CString::new(msg).ok();
        } else {
            eprintln!("FATAL: RefCell panic in cyt error handling");
            std::process::abort();
        }
    });
}

pub fn clear_error() {
    LAST_ERROR.with(|e| {
        if let Ok(mut err) = e.try_borrow_mut() {
            *err = None;
        } else {
            eprintln!("FATAL: RefCell panic in cyt error clearing");
            std::process::abort();
        }
    });
}

/// Get the last error message for the current thread.
///
/// Returns NULL if no error occurred. Valid until the next `cyt_*` call on this thread.
///
/// # Safety
///
/// No pointer arguments; safe to call from C when linked against this library.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_last_error() -> *const c_char {
    LAST_ERROR.with(|e| {
        (*e.borrow())
            .as_ref()
            .map_or(ptr::null(), |cstr| cstr.as_ptr())
    })
}

/// Clear the last error for the current thread.
///
/// # Safety
///
/// No pointer arguments; safe to call from C when linked against this library.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_clear_error() {
    clear_error();
}
