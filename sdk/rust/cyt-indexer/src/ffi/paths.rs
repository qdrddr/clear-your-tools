//! Path configuration FFI exports.

use crate::ffi::error::{CYT_ERR_NULL_PTR, set_error};
use crate::ffi::json_util::{
    c_str_to_str, parse_json_cstr, run_ffi, write_json_out, write_optional_string_out,
    write_string_result,
};
use crate::paths::{self, PathConfig};
use std::os::raw::{c_char, c_int};
use std::path::PathBuf;

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_configure_path_constants(
    md_ext: *const c_char,
    json_ext: *const c_char,
    decomposed_prefix: *const c_char,
    decomposed_root: *const c_char,
    catalog_prefix: *const c_char,
    default_catalog_dir: *const c_char,
    builder_memory_only: c_int,
    write_catalog_prune: c_int,
) -> c_int {
    run_ffi(|| {
        let defaults = PathConfig::default();
        paths::configure(PathConfig {
            md_ext: unsafe { c_str_to_str(md_ext, "md_ext")? }.to_string(),
            json_ext: unsafe { c_str_to_str(json_ext, "json_ext")? }.to_string(),
            decomposed_prefix: unsafe { c_str_to_str(decomposed_prefix, "decomposed_prefix")? }
                .to_string(),
            decomposed_root: PathBuf::from(unsafe {
                c_str_to_str(decomposed_root, "decomposed_root")?
            }),
            skills_decomposed_prefix: defaults.skills_decomposed_prefix,
            skills_decomposed_root: defaults.skills_decomposed_root,
            catalog_prefix: unsafe { c_str_to_str(catalog_prefix, "catalog_prefix")? }.to_string(),
            builder_memory_only: builder_memory_only != 0,
            default_catalog_dir: PathBuf::from(unsafe {
                c_str_to_str(default_catalog_dir, "default_catalog_dir")?
            }),
            write_catalog_prune: write_catalog_prune != 0,
        });
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_collect_enums(
    schema_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(schema_json, "schema_json")? };
        let found = paths::collect_enums(&val);
        unsafe { write_json_out(&serde_json::Value::Array(found), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_to_decomposed_key(
    file_path: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let path = unsafe { c_str_to_str(file_path, "file_path")? };
        unsafe { write_optional_string_out(paths::to_decomposed_key(path), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_tool_id_from_decomposed_rel(
    rel_path: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let rel = unsafe { c_str_to_str(rel_path, "rel_path")? };
        unsafe {
            write_string_result(&paths::tool_id_from_decomposed_rel(rel), out)?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_root_tool_key(
    file_path: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let path = unsafe { c_str_to_str(file_path, "file_path")? };
        unsafe { write_optional_string_out(paths::get_root_tool_key(path), out)? };
        Ok(())
    })
}

macro_rules! path_getter {
    ($fn:ident, $body:expr) => {
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $fn(out: *mut *mut c_char) -> c_int {
            crate::ffi::json_util::run_ffi(|| {
                if out.is_null() {
                    crate::ffi::error::set_error("null pointer: out");
                    return Err(crate::ffi::error::CYT_ERR_NULL_PTR);
                }
                unsafe {
                    crate::ffi::json_util::write_string_result(&$body, out)?;
                }
                Ok(())
            })
        }
    };
}

path_getter!(cyt_path_md_ext, paths::md_ext());
path_getter!(cyt_path_json_ext, paths::json_ext());
path_getter!(cyt_path_decomposed_prefix, paths::decomposed_prefix());
path_getter!(
    cyt_path_decomposed_root,
    paths::decomposed_root().to_string_lossy()
);
path_getter!(cyt_path_catalog_prefix, paths::catalog_prefix());
path_getter!(
    cyt_path_default_catalog_dir,
    paths::default_catalog_dir().to_string_lossy()
);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_builder_memory_only() -> c_int {
    i32::from(paths::builder_memory_only())
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_path_write_catalog_prune() -> c_int {
    i32::from(paths::write_catalog_prune())
}
