//! Runtime configuration FFI exports.

use crate::ffi::error::{CYT_ERR_NULL_PTR, clear_error, set_error};
use crate::ffi::json_util::{c_str_to_str, run_ffi, write_string_result};
use crate::runtime_config::{self, RuntimeConfig};
use std::os::raw::{c_char, c_int};

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_configure_runtime_defaults(
    decomposed_score: f64,
    enum_score: f64,
    rerank_score: f64,
    empty_optional_fallback_k: usize,
    default_system_policy: *const c_char,
    default_mcp_policy: *const c_char,
) -> c_int {
    run_ffi(|| {
        runtime_config::configure(RuntimeConfig {
            decomposed_score,
            enum_score,
            rerank_score,
            empty_optional_fallback_k,
            default_system_policy: unsafe {
                c_str_to_str(default_system_policy, "default_system_policy")?
            }
            .to_string(),
            default_mcp_policy: unsafe { c_str_to_str(default_mcp_policy, "default_mcp_policy")? }
                .to_string(),
        });
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_decomposed_score() -> f64 {
    runtime_config::decomposed_score()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_enum_score() -> f64 {
    runtime_config::enum_score()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_rerank_score() -> f64 {
    runtime_config::rerank_score()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_empty_optional_fallback_k() -> usize {
    runtime_config::empty_optional_fallback_k()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_default_system_policy(out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        unsafe {
            write_string_result(&runtime_config::default_system_policy(), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_runtime_default_mcp_policy(out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        unsafe {
            write_string_result(&runtime_config::default_mcp_policy(), out)?;
        }
        Ok(())
    })
}
