//! BM25 skills registry ensure/build/load under ``entries/{content_sha256}/``.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::pageindex::cache_layout::nodes_dir;
use crate::pageindex::cache_layout::skill_entry_dir;
use crate::pageindex::{
    EntryMetadata, PageIndexConfig, SkillDocument, SkillsIndex, build_chunk_variant,
    build_page_index_for_content, build_page_index_for_file, chunk_variant_valid,
    load_merged_document_json, page_index_valid, write_page_index_entry,
};
use crate::skills_io::refresh_skills_index_cache;

use super::config::memory_cache_config;
use super::disk_writer::maybe_enqueue_skills_index;
use super::hot::{get_merged_document, store_merged_document, store_skills_index};
use super::manifest::CacheStatus;
use super::materialize::stub_document_from_source;
use super::{CachePolicy, disk_available, expand_tilde};

#[derive(Debug, Clone)]
pub struct SkillSourceSpec {
    path: PathBuf,
    content: Option<String>,
    content_sha256: Option<String>,
}

pub fn parse_skill_sources(source_paths: &[PathBuf]) -> Vec<SkillSourceSpec> {
    source_paths
        .iter()
        .map(|path| SkillSourceSpec {
            path: path.clone(),
            content: None,
            content_sha256: None,
        })
        .collect()
}

#[cfg(any(feature = "ffi", feature = "python", feature = "node", test))]
fn parse_skill_sources_json(values: &[serde_json::Value]) -> Result<Vec<SkillSourceSpec>, String> {
    let mut specs = Vec::new();
    for value in values {
        if let Some(path_str) = value.as_str() {
            specs.push(SkillSourceSpec {
                path: PathBuf::from(path_str),
                content: None,
                content_sha256: None,
            });
            continue;
        }
        let Some(obj) = value.as_object() else {
            continue;
        };
        let path = obj
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "skill source object missing path".to_string())?;
        let content = obj
            .get("content")
            .and_then(serde_json::Value::as_str)
            .map(str::to_string);
        let content_sha256 = obj
            .get("content_sha256")
            .and_then(serde_json::Value::as_str)
            .map(str::to_string);
        specs.push(SkillSourceSpec {
            path: PathBuf::from(path),
            content,
            content_sha256,
        });
    }
    Ok(specs)
}

#[derive(Debug, Clone)]
pub struct SkillEntryRef {
    pub entry_dir: PathBuf,
    pub doc_id: String,
    pub content_sha256: String,
    pub bm25_chunk_dir: Option<PathBuf>,
    pub disk_backed: bool,
    pub cache_status: CacheStatus,
    pub source_path: String,
    pub nodes_dir: Option<PathBuf>,
    pub document: Option<Value>,
    /// True when lazy registry deferred full page/chunk indexing.
    pub lazy_pending: bool,
}

fn file_content_hash(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    Ok(content_hash_bytes(&bytes))
}

fn content_hash_bytes(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

fn content_hash_text(content: &str) -> String {
    content_hash_bytes(content.as_bytes())
}

fn doc_id_from_path(path: &Path) -> String {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("skill")
        .to_string()
        .replace('/', "__")
        .to_lowercase()
}

fn persist_skills_index(entry_dir: &Path, doc_id: &str, index: &SkillsIndex) {
    store_skills_index(entry_dir, doc_id, None, index.clone());
    maybe_enqueue_skills_index(entry_dir.to_path_buf(), index.clone());
}

fn ensure_bm25_chunk_variant(
    entry_dir: &Path,
    doc_id: &str,
    bm25_chunk_dir: Option<&Path>,
    index_params_hash: &str,
    pageindex_config: &PageIndexConfig,
) -> Result<(), String> {
    if bm25_chunk_dir.is_none() {
        return Ok(());
    }
    if chunk_variant_valid(entry_dir, "bm25", index_params_hash, doc_id) {
        return Ok(());
    }
    build_chunk_variant(
        entry_dir,
        doc_id,
        "bm25",
        index_params_hash,
        pageindex_config,
    )?;
    refresh_skills_index_cache(entry_dir, doc_id, bm25_chunk_dir);
    Ok(())
}

fn load_document_cached(
    entry_dir: &Path,
    doc_id: &str,
    bm25_chunk_dir: Option<&Path>,
) -> Result<Value, String> {
    if let Some(doc) = get_merged_document(entry_dir, bm25_chunk_dir) {
        return Ok(doc);
    }
    let document = load_merged_document_json(entry_dir, doc_id, bm25_chunk_dir)?;
    Ok(store_merged_document(entry_dir, bm25_chunk_dir, document))
}

#[allow(clippy::too_many_arguments)]
fn ensure_one_skill_entry(
    spec: &SkillSourceSpec,
    catalog_root: &Path,
    pageindex_config: &PageIndexConfig,
    _pipeline: &str,
    index_params_hash: &str,
    _policy: CachePolicy,
    disk_ok: bool,
    materialize_bm25: bool,
    lazy_registry: bool,
) -> Result<SkillEntryRef, String> {
    let source = &spec.path;
    let content_sha256 = match spec.content_sha256.as_deref() {
        Some(hash) if !hash.is_empty() => hash.to_string(),
        _ => match spec.content.as_deref() {
            Some(content) => content_hash_text(content),
            None => file_content_hash(source)?,
        },
    };
    let doc_id = doc_id_from_path(source);
    let entry_dir = skill_entry_dir(catalog_root, &content_sha256);
    let bm25_chunk_dir = if materialize_bm25 {
        Some(
            entry_dir
                .join("chunks")
                .join("bm25")
                .join(index_params_hash),
        )
    } else {
        None
    };

    let mut disk_backed = false;
    let mut cache_status = CacheStatus::MemoryFallback;
    let mut lazy_pending = false;
    let document;

    if disk_ok && page_index_valid(&entry_dir, &content_sha256) {
        cache_status = CacheStatus::Hit;
        disk_backed = true;
        document = Some(load_document_cached(
            &entry_dir,
            &doc_id,
            bm25_chunk_dir.as_deref(),
        )?);
        if materialize_bm25 && !chunk_variant_valid(&entry_dir, "bm25", index_params_hash, &doc_id)
        {
            ensure_bm25_chunk_variant(
                &entry_dir,
                &doc_id,
                bm25_chunk_dir.as_deref(),
                index_params_hash,
                pageindex_config,
            )?;
            cache_status = CacheStatus::Miss;
        }
    } else if lazy_registry && spec.content.is_none() {
        lazy_pending = true;
        document = Some(stub_document_from_source(source, &doc_id)?);
    } else {
        let index = if let Some(ref content) = spec.content {
            build_page_index_for_content(source, content, pageindex_config)?
        } else {
            build_page_index_for_file(source, pageindex_config)?
        };

        if disk_ok {
            let metadata = EntryMetadata {
                source_path: source.display().to_string(),
                pipeline: String::new(),
                index_params: serde_json::Value::Null,
            };
            write_page_index_entry(&index, &entry_dir, &doc_id, Some(&metadata))?;
            persist_skills_index(&entry_dir, &doc_id, &index);
            disk_backed = true;
            cache_status = CacheStatus::Miss;

            if materialize_bm25 {
                ensure_bm25_chunk_variant(
                    &entry_dir,
                    &doc_id,
                    bm25_chunk_dir.as_deref(),
                    index_params_hash,
                    pageindex_config,
                )?;
            }
            document = Some(load_document_cached(
                &entry_dir,
                &doc_id,
                bm25_chunk_dir.as_deref(),
            )?);
        } else {
            document = index.documents.get(&doc_id).map(SkillDocument::to_json);
            if let Some(ref doc) = document {
                let _ = store_merged_document(&entry_dir, bm25_chunk_dir.as_deref(), doc.clone());
            }
        }
    }

    let nodes_dir_path = if disk_backed {
        Some(nodes_dir(&entry_dir))
    } else {
        None
    };

    Ok(SkillEntryRef {
        entry_dir,
        doc_id,
        content_sha256,
        bm25_chunk_dir,
        disk_backed,
        cache_status,
        source_path: source.display().to_string(),
        nodes_dir: nodes_dir_path,
        document,
        lazy_pending,
    })
}

/// Ensure page index + optional BM25 chunk variant for each source skill file.
///
/// `source_paths` may be filesystem paths, or a JSON array of path strings / objects with
/// `{path, content, content_sha256}` for in-memory client-supplied skills.
///
/// When ``memory_cache_config().lazy_registry`` is true, entries without a disk hit
/// return frontmatter-only stubs and defer full indexing to first BM25 use.
///
/// # Errors
///
/// Returns an error when a source file cannot be read or indexed.
pub fn ensure_skills_registry(
    source_paths: &[PathBuf],
    catalog_root: &Path,
    pageindex_config: &PageIndexConfig,
    pipeline: &str,
    index_params_hash: &str,
    policy: CachePolicy,
) -> Result<Vec<SkillEntryRef>, String> {
    ensure_skills_registry_from_specs(
        &parse_skill_sources(source_paths),
        catalog_root,
        pageindex_config,
        pipeline,
        index_params_hash,
        policy,
    )
}

/// Ensure page index entries from parsed source specs (paths and optional in-memory content).
///
/// # Errors
///
/// Returns an error when a source cannot be indexed.
pub fn ensure_skills_registry_from_specs(
    sources: &[SkillSourceSpec],
    catalog_root: &Path,
    pageindex_config: &PageIndexConfig,
    pipeline: &str,
    index_params_hash: &str,
    policy: CachePolicy,
) -> Result<Vec<SkillEntryRef>, String> {
    let root = expand_tilde(catalog_root);
    let disk_ok = policy != CachePolicy::ForceMemory && disk_available(&root);
    let materialize_bm25 = pipeline.trim().eq_ignore_ascii_case("bm25");
    let lazy_registry = memory_cache_config().lazy_registry;
    let mut refs = Vec::new();

    for spec in sources {
        if spec.content.is_none() && !spec.path.is_file() {
            continue;
        }
        refs.push(ensure_one_skill_entry(
            spec,
            &root,
            pageindex_config,
            pipeline,
            index_params_hash,
            policy,
            disk_ok,
            materialize_bm25,
            lazy_registry,
        )?);
    }

    Ok(refs)
}

/// Parse hook/client skill source JSON into registry specs.
///
/// # Errors
///
/// Returns an error when an object entry is missing required fields.
#[cfg(any(feature = "ffi", feature = "python", feature = "node", test))]
pub fn parse_skill_source_specs_json(
    values: &[serde_json::Value],
) -> Result<Vec<SkillSourceSpec>, String> {
    parse_skill_sources_json(values)
}
