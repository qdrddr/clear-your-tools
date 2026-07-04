//! `PageIndex` / skills FFI exports (mirrors `pageindex_python.rs`).

use crate::ffi::error::{
    CYT_ERR_INVALID_ARG, CYT_ERR_INVALID_HANDLE, CYT_ERR_IO, CYT_ERR_NULL_PTR, clear_error,
    set_error,
};
use crate::ffi::json_util::{
    c_str_to_str, json_array_or_empty, parse_json_cstr, run_ffi, write_json_out,
    write_string_result,
};
use crate::pageindex::document_json::{
    finalize_entry_metadata, load_merged_document_json, update_document_source_path,
};
use crate::pageindex::spec_refs::OwnedSpecRefs;
use crate::pageindex::{
    EntryMetadata, PageIndexConfig, ReconstructOptions, SkillDocument, SkillsIndex,
    build_chunk_variant, build_page_index_only, build_skills_index, chunk_variant_valid,
    get_content_retrieve_result, get_document, get_document_structure, get_line_content,
    get_line_content_from_spec, md_to_tree, page_index_valid, parse_chunk_ids, parse_node_ids,
    reconstruct_skill_markdown, repair_skill_chunks, repair_skill_variant_chunks,
    write_reconstructed_skill,
};
use crate::skills_builder::SkillsBuilder;
use crate::skills_io::{
    load_skills_index_from_dir, load_skills_index_from_entry, skills_index_from_decomposed_dir,
    write_skills_index,
};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::os::raw::{c_char, c_int};
use std::path::PathBuf;

/// Opaque skills builder handle.
pub struct CytSkillsBuilder {
    pub(crate) inner: SkillsBuilder,
}

fn page_index_config_from_json(config_json: *const c_char) -> Result<PageIndexConfig, c_int> {
    if config_json.is_null() {
        return Ok(PageIndexConfig::default());
    }
    let val = unsafe { parse_json_cstr(config_json, "config_json")? };
    Ok(PageIndexConfig::from_value(&val))
}

fn reconstruct_options_from_json(options_json: *const c_char) -> Result<ReconstructOptions, c_int> {
    if options_json.is_null() {
        return Ok(ReconstructOptions::default());
    }
    let val = unsafe { parse_json_cstr(options_json, "options_json")? };
    Ok(ReconstructOptions {
        keep_all_headers: val
            .get("keep_all_headers")
            .or_else(|| val.get("keepAllHeaders"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
    })
}

fn skills_index_to_json(index: &SkillsIndex) -> Value {
    json!({
        "documents": index.documents_as_json(),
        "files": index.files,
    })
}

fn skills_index_from_json(val: &Value) -> Result<SkillsIndex, c_int> {
    if val.get("documents").is_some() || val.get("files").is_some() {
        let mut skills = SkillsIndex::default();
        if let Some(docs) = val.get("documents").and_then(Value::as_object) {
            for (doc_id, doc_val) in docs {
                if let Some(doc) = SkillDocument::from_json(doc_val) {
                    skills.documents.insert(doc_id.clone(), doc);
                }
            }
        }
        if let Some(files) = val.get("files").and_then(Value::as_object) {
            for (k, v) in files {
                if let Some(s) = v.as_str() {
                    skills.files.insert(k.clone(), s.to_string());
                }
            }
        }
        return Ok(skills);
    }

    let mut skills = SkillsIndex::default();
    let obj = val.as_object().ok_or_else(|| {
        set_error("documents must be a JSON object");
        CYT_ERR_INVALID_ARG
    })?;
    for (doc_id, doc_val) in obj {
        let Some(doc) = SkillDocument::from_json(doc_val) else {
            set_error(&format!("invalid document {doc_id}"));
            return Err(CYT_ERR_INVALID_ARG);
        };
        skills.documents.insert(doc_id.clone(), doc);
    }
    Ok(skills)
}

fn documents_map_from_json(val: &Value) -> Result<HashMap<String, SkillDocument>, c_int> {
    let obj = val.as_object().ok_or_else(|| {
        set_error("documents must be a JSON object");
        CYT_ERR_INVALID_ARG
    })?;
    let mut out = HashMap::new();
    for (doc_id, doc_val) in obj {
        let Some(doc) = SkillDocument::from_json(doc_val) else {
            set_error(&format!("invalid document {doc_id}"));
            return Err(CYT_ERR_INVALID_ARG);
        };
        out.insert(doc_id.clone(), doc);
    }
    Ok(out)
}

fn skill_dirs_from_json(skill_dirs_json: *const c_char) -> Result<Vec<PathBuf>, c_int> {
    let dirs_val = unsafe { parse_json_cstr(skill_dirs_json, "skill_dirs_json")? };
    Ok(json_array_or_empty(&dirs_val)
        .into_iter()
        .filter_map(|v| v.as_str().map(PathBuf::from))
        .collect())
}

fn string_specs_from_json(
    specs_json: *const c_char,
    name: &str,
) -> Result<Option<Vec<String>>, c_int> {
    if specs_json.is_null() {
        return Ok(None);
    }
    let val = unsafe { parse_json_cstr(specs_json, name)? };
    Ok(Some(
        json_array_or_empty(&val)
            .into_iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
    ))
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_build_skills_index(
    skill_dirs_json: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dirs = skill_dirs_from_json(skill_dirs_json)?;
        let cfg = page_index_config_from_json(config_json)?;
        let index = build_skills_index(&dirs, &cfg).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_write_skills_index(
    index_json: *const c_char,
    output_dir: *const c_char,
) -> c_int {
    run_ffi(|| {
        let val = unsafe { parse_json_cstr(index_json, "index_json")? };
        let skills = skills_index_from_json(&val)?;
        let dir = PathBuf::from(unsafe { c_str_to_str(output_dir, "output_dir")? });
        write_skills_index(&skills, dir.as_path()).map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_load_skills_index_from_dir(
    catalog_dir: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dir = PathBuf::from(unsafe { c_str_to_str(catalog_dir, "catalog_dir")? });
        let index = load_skills_index_from_dir(dir.as_path()).map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_repair_skill_chunks(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    config_json: *const c_char,
) -> c_int {
    run_ffi(|| {
        let dir = PathBuf::from(unsafe { c_str_to_str(entry_dir, "entry_dir")? });
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let cfg = page_index_config_from_json(config_json)?;
        repair_skill_chunks(dir.as_path(), doc, &cfg).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_index_from_decomposed_dir(
    dir: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let path = PathBuf::from(unsafe { c_str_to_str(dir, "dir")? });
        let index = skills_index_from_decomposed_dir(path.as_path()).map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_md_to_tree(
    markdown_content: *const c_char,
    source_path: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let md = unsafe { c_str_to_str(markdown_content, "markdown_content")? };
        let source = unsafe { c_str_to_str(source_path, "source_path")? };
        let cfg = page_index_config_from_json(config_json)?;
        let result = md_to_tree(md, source, &cfg);
        unsafe {
            write_json_out(
                &json!({
                    "doc_name": result.doc_name,
                    "line_count": result.line_count,
                    "structure": result.structure,
                }),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_skill_document(
    documents_json: *const c_char,
    doc_id: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(documents_json, "documents_json")? };
        let docs = documents_map_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        unsafe { write_json_out(&get_document(&docs, doc), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_skill_structure(
    documents_json: *const c_char,
    doc_id: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(documents_json, "documents_json")? };
        let docs = documents_map_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        unsafe { write_json_out(&get_document_structure(&docs, doc), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_skill_line_content_from_spec(
    index_or_docs_json: *const c_char,
    doc_id: *const c_char,
    line_num_spec: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_or_docs_json, "index_or_docs_json")? };
        let index = skills_index_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let spec = unsafe { c_str_to_str(line_num_spec, "line_num_spec")? };
        unsafe { write_json_out(&get_line_content_from_spec(&index, doc, spec), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_skill_content_retrieve_result(
    index_or_docs_json: *const c_char,
    doc_id: *const c_char,
    line_num_specs_json: *const c_char,
    node_id_specs_json: *const c_char,
    chunk_id_specs_json: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_or_docs_json, "index_or_docs_json")? };
        let index = skills_index_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let line_specs = string_specs_from_json(line_num_specs_json, "line_num_specs_json")?;
        let node_specs = string_specs_from_json(node_id_specs_json, "node_id_specs_json")?;
        let chunk_specs = string_specs_from_json(chunk_id_specs_json, "chunk_id_specs_json")?;
        let specs = OwnedSpecRefs::new(line_specs, node_specs, chunk_specs);
        let opts = reconstruct_options_from_json(options_json)?;
        unsafe {
            write_json_out(
                &get_content_retrieve_result(
                    &index,
                    doc,
                    &specs.line_refs(),
                    &specs.node_refs(),
                    &specs.chunk_refs(),
                    &opts,
                ),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_reconstruct_skill_markdown(
    index_or_docs_json: *const c_char,
    doc_id: *const c_char,
    line_num_specs_json: *const c_char,
    node_id_specs_json: *const c_char,
    chunk_id_specs_json: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_or_docs_json, "index_or_docs_json")? };
        let index = skills_index_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let line_specs = string_specs_from_json(line_num_specs_json, "line_num_specs_json")?;
        let node_specs = string_specs_from_json(node_id_specs_json, "node_id_specs_json")?;
        let chunk_specs = string_specs_from_json(chunk_id_specs_json, "chunk_id_specs_json")?;
        let specs = OwnedSpecRefs::new(line_specs, node_specs, chunk_specs);
        let opts = reconstruct_options_from_json(options_json)?;
        let result = reconstruct_skill_markdown(
            &index,
            doc,
            &specs.line_refs(),
            &specs.node_refs(),
            &specs.chunk_refs(),
            &opts,
        )
        .map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe {
            write_json_out(
                &json!({
                    "markdown": result.markdown,
                    "matched_node_ids": result.matched_node_ids,
                    "matched_chunk_ids": result.matched_chunk_ids,
                    "node_ids": result.node_ids,
                    "output_rel_path": result.output_rel_path,
                }),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_write_reconstructed_skill(
    catalog_dir: *const c_char,
    index_or_docs_json: *const c_char,
    doc_id: *const c_char,
    line_num_specs_json: *const c_char,
    node_id_specs_json: *const c_char,
    chunk_id_specs_json: *const c_char,
    options_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dir = PathBuf::from(unsafe { c_str_to_str(catalog_dir, "catalog_dir")? });
        let val = unsafe { parse_json_cstr(index_or_docs_json, "index_or_docs_json")? };
        let index = skills_index_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let line_specs = string_specs_from_json(line_num_specs_json, "line_num_specs_json")?;
        let node_specs = string_specs_from_json(node_id_specs_json, "node_id_specs_json")?;
        let chunk_specs = string_specs_from_json(chunk_id_specs_json, "chunk_id_specs_json")?;
        let specs = OwnedSpecRefs::new(line_specs, node_specs, chunk_specs);
        let opts = reconstruct_options_from_json(options_json)?;
        let output = write_reconstructed_skill(
            dir.as_path(),
            &index,
            doc,
            &specs.line_refs(),
            &specs.node_refs(),
            &specs.chunk_refs(),
            &opts,
        )
        .map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_string_result(&output.display().to_string(), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_get_skill_line_content(
    index_or_docs_json: *const c_char,
    doc_id: *const c_char,
    line_num_specs_json: *const c_char,
    node_id_specs_json: *const c_char,
    chunk_id_specs_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { parse_json_cstr(index_or_docs_json, "index_or_docs_json")? };
        let index = skills_index_from_json(&val)?;
        let doc = unsafe { c_str_to_str(doc_id, "doc_id")? };
        let line_specs = string_specs_from_json(line_num_specs_json, "line_num_specs_json")?;
        let node_specs = string_specs_from_json(node_id_specs_json, "node_id_specs_json")?;
        let chunk_specs = string_specs_from_json(chunk_id_specs_json, "chunk_id_specs_json")?;
        let specs = OwnedSpecRefs::new(line_specs, node_specs, chunk_specs);
        unsafe {
            write_json_out(
                &get_line_content(
                    &index,
                    doc,
                    &specs.line_refs(),
                    &specs.node_refs(),
                    &specs.chunk_refs(),
                ),
                out,
            )?;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_parse_skill_chunk_ids(
    spec: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let s = unsafe { c_str_to_str(spec, "spec")? };
        let ids = parse_chunk_ids(s).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&json!(ids), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_parse_skill_node_ids(
    spec: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let s = unsafe { c_str_to_str(spec, "spec")? };
        let ids = parse_node_ids(s).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&json!(ids), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_new(
    memory_only: c_int,
    output_dir: *const c_char,
    out: *mut *mut CytSkillsBuilder,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dir = if output_dir.is_null() {
            None
        } else {
            Some(PathBuf::from(unsafe {
                c_str_to_str(output_dir, "output_dir")?
            }))
        };
        unsafe {
            *out = Box::into_raw(Box::new(CytSkillsBuilder {
                inner: SkillsBuilder::new(memory_only != 0, dir),
            }));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_free(builder: *mut CytSkillsBuilder) {
    if !builder.is_null() {
        unsafe {
            let _ = Box::from_raw(builder);
        }
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_build_from_dirs(
    builder: *mut CytSkillsBuilder,
    skill_dirs_json: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if builder.is_null() {
            set_error("null pointer: builder");
            return Err(CYT_ERR_INVALID_HANDLE);
        }
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let dirs = skill_dirs_from_json(skill_dirs_json)?;
        let cfg = page_index_config_from_json(config_json)?;
        let index = unsafe { (*builder).inner.build_from_dirs(&dirs, &cfg) }.map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&skills_index_to_json(index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_write_catalog(
    builder: *mut CytSkillsBuilder,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if builder.is_null() {
            set_error("null pointer: builder");
            return Err(CYT_ERR_INVALID_HANDLE);
        }
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let index = unsafe { (*builder).inner.write_catalog() }.map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_json_out(&skills_index_to_json(index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_to_skills_index_json(
    builder: *const CytSkillsBuilder,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if builder.is_null() {
            set_error("null pointer: builder");
            return Err(CYT_ERR_INVALID_HANDLE);
        }
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { (*builder).inner.to_skills_index_json() }.ok_or_else(|| {
            set_error("index not built");
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&val, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_skills_builder_to_skills_dict(
    builder: *const CytSkillsBuilder,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if builder.is_null() {
            set_error("null pointer: builder");
            return Err(CYT_ERR_INVALID_HANDLE);
        }
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        let val = unsafe { (*builder).inner.to_skills_dict() }.ok_or_else(|| {
            set_error("index not built");
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&val, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_reconstruct_options_default(out: *mut *mut c_char) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            set_error("null pointer: out");
            return Err(CYT_ERR_NULL_PTR);
        }
        unsafe { write_json_out(&json!({ "keep_all_headers": false }), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_build_page_index_only(
    skill_dirs_json: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let dirs_val = parse_json_cstr(skill_dirs_json, "skill_dirs_json")?;
        let dirs: Vec<PathBuf> = dirs_val
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter_map(|v| v.as_str().map(PathBuf::from))
            .collect();
        let cfg = page_index_config_from_json(config_json)?;
        let index = build_page_index_only(&dirs, &cfg).map_err(|e| {
            set_error(&e);
            CYT_ERR_INVALID_ARG
        })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_build_chunk_variant(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    pipeline: *const c_char,
    params_hash: *const c_char,
    config_json: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let pipe = c_str_to_str(pipeline, "pipeline")?;
        let hash = c_str_to_str(params_hash, "params_hash")?;
        let cfg = page_index_config_from_json(config_json)?;
        let index = build_chunk_variant(PathBuf::from(entry).as_path(), doc, pipe, hash, &cfg)
            .map_err(|e| {
                set_error(&e);
                CYT_ERR_INVALID_ARG
            })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_page_index_valid(
    entry_dir: *const c_char,
    content_sha256: *const c_char,
    out: *mut c_int,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let hash = c_str_to_str(content_sha256, "content_sha256")?;
        unsafe {
            *out = i32::from(page_index_valid(PathBuf::from(entry).as_path(), hash));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_chunk_variant_valid(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    pipeline: *const c_char,
    params_hash: *const c_char,
    out: *mut c_int,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let pipe = c_str_to_str(pipeline, "pipeline")?;
        let hash = c_str_to_str(params_hash, "params_hash")?;
        unsafe {
            *out = i32::from(chunk_variant_valid(
                PathBuf::from(entry).as_path(),
                doc,
                pipe,
                hash,
            ));
        }
        clear_error();
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_repair_skill_variant_chunks(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    pipeline: *const c_char,
    params_hash: *const c_char,
    config_json: *const c_char,
) -> c_int {
    run_ffi(|| {
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let pipe = c_str_to_str(pipeline, "pipeline")?;
        let hash = c_str_to_str(params_hash, "params_hash")?;
        let cfg = page_index_config_from_json(config_json)?;
        repair_skill_variant_chunks(PathBuf::from(entry).as_path(), doc, pipe, hash, &cfg)
            .map_err(|e| {
                set_error(&e);
                CYT_ERR_IO
            })?;
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_load_skills_index_from_entry(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    chunk_dir: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let chunk_path = if chunk_dir.is_null() {
            None
        } else {
            Some(PathBuf::from(c_str_to_str(chunk_dir, "chunk_dir")?))
        };
        let index = load_skills_index_from_entry(
            PathBuf::from(entry).as_path(),
            doc,
            chunk_path.as_deref(),
        )
        .map_err(|e| {
            set_error(&e);
            CYT_ERR_IO
        })?;
        unsafe { write_json_out(&skills_index_to_json(&index), out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_load_merged_skill_document_json(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    chunk_dir: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let chunk_path = if chunk_dir.is_null() {
            None
        } else {
            Some(PathBuf::from(c_str_to_str(chunk_dir, "chunk_dir")?))
        };
        let value =
            load_merged_document_json(PathBuf::from(entry).as_path(), doc, chunk_path.as_deref())
                .map_err(|e| {
                set_error(&e);
                CYT_ERR_IO
            })?;
        unsafe { write_json_out(&value, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_finalize_skill_document_json(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    content_sha256: *const c_char,
    pipeline: *const c_char,
    index_params_json: *const c_char,
    built_at: *const c_char,
    source_path: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let _ = doc;
        let metadata = EntryMetadata {
            source_path: c_str_to_str(source_path, "source_path")?.to_string(),
            pipeline: c_str_to_str(pipeline, "pipeline")?.to_string(),
            index_params: parse_json_cstr(index_params_json, "index_params_json")?,
        };
        let _ = (content_sha256, built_at);
        let value =
            finalize_entry_metadata(PathBuf::from(entry).as_path(), &metadata).map_err(|e| {
                set_error(&e);
                CYT_ERR_IO
            })?;
        unsafe { write_json_out(&value, out)? };
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn cyt_update_skill_document_source_path(
    entry_dir: *const c_char,
    doc_id: *const c_char,
    source_path: *const c_char,
    out: *mut *mut c_char,
) -> c_int {
    run_ffi(|| {
        if out.is_null() {
            return Err(CYT_ERR_NULL_PTR);
        }
        let entry = c_str_to_str(entry_dir, "entry_dir")?;
        let doc = c_str_to_str(doc_id, "doc_id")?;
        let path = c_str_to_str(source_path, "source_path")?;
        let value = update_document_source_path(PathBuf::from(entry).as_path(), doc, path)
            .map_err(|e| {
                set_error(&e);
                CYT_ERR_IO
            })?;
        unsafe { write_json_out(&value, out)? };
        Ok(())
    })
}
