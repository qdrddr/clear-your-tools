//! BM25 cohesion chunking FFI exports.

use crate::bm25_cohesion::{Bm25CohesionChunker, Bm25CohesionConfig};
use crate::ffi::error::{CYT_ERR_INVALID_ARG, CYT_ERR_NULL_PTR, set_error};
use crate::ffi::json_util::{c_str_to_str, parse_json_cstr, run_ffi, write_json_out};
use serde_json::json;
use std::os::raw::{c_char, c_int};

/// Return default BM25 cohesion config as JSON.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_cohesion_default_config(out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let cfg = Bm25CohesionConfig::default();
        let val = json!({
            "window_mode": "sentence",
            "threshold": cfg.threshold,
            "merge_threshold": cfg.merge_threshold,
            "chunk_size": cfg.chunk_size,
            "token_counter": "tiktoken",
            "similarity_window": cfg.similarity_window,
            "next_unit_size": cfg.next_unit_size,
            "skip_window": cfg.skip_window,
            "min_units_per_chunk": cfg.min_units_per_chunk,
            "minimum_words": cfg.minimum_words,
            "minimum_sentences": cfg.minimum_sentences,
            "use_stopwords": cfg.use_stopwords,
        });
        unsafe { write_json_out(&val, out)? };
        Ok(())
    })
}

/// Chunk text with BM25 cohesion segmentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_bm25_cohesion_chunk(
    text: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let input = unsafe { c_str_to_str(text, "text")? };
        let cfg = if config_json.is_null() {
            Bm25CohesionConfig::default()
        } else {
            let val = unsafe { parse_json_cstr(config_json, "config_json")? };
            Bm25CohesionConfig::from_partial(&val)
        };
        let chunker = Bm25CohesionChunker::new(cfg).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        let chunks: Vec<serde_json::Value> = chunker
            .chunk(input)
            .into_iter()
            .map(|c| {
                json!({
                    "text": c.text,
                    "start_index": c.start_index,
                    "end_index": c.end_index,
                    "token_count": c.token_count,
                })
            })
            .collect();
        unsafe { write_json_out(&serde_json::Value::Array(chunks), out)? };
        Ok(())
    })
}
