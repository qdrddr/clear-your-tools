//! BM25 skills registry ensure/build/load under ``entries/{content_sha256}/``.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::pageindex::cache_layout::nodes_dir;
use crate::pageindex::cache_layout::skill_entry_dir;
use crate::pageindex::{
    PageIndexConfig, SkillDocument, SkillDocumentExtras, SkillsIndex, build_chunk_variant,
    build_page_index_only, chunk_variant_valid, load_merged_document_json, page_index_valid,
    write_page_index_entry,
};
use crate::skills_io::refresh_skills_index_cache;

use super::config::memory_cache_config;
use super::disk_writer::maybe_enqueue_skills_index;
use super::hot::{get_merged_document, store_merged_document, store_skills_index};
use super::manifest::CacheStatus;
use super::materialize::stub_document_from_source;
use super::{CachePolicy, disk_available, expand_tilde};

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
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    Ok(hex::encode(hasher.finalize()))
}

fn doc_id_from_path(path: &Path) -> String {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("skill")
        .to_string()
        .replace('/', "__")
        .to_lowercase()
}

fn build_page_index_for_source(
    source: &Path,
    pageindex_config: &PageIndexConfig,
) -> Result<SkillsIndex, String> {
    let file_name = source
        .file_name()
        .ok_or_else(|| "skill source path has no file name".to_string())?;
    let tmp = std::env::temp_dir().join(format!(
        "cyt-skill-{}-{}",
        std::process::id(),
        file_name.to_string_lossy()
    ));
    if tmp.exists() {
        let _ = fs::remove_dir_all(&tmp);
    }
    fs::create_dir_all(&tmp).map_err(|e| e.to_string())?;
    let dest = tmp.join(file_name);
    fs::copy(source, &dest).map_err(|e| e.to_string())?;
    let result = build_page_index_only(std::slice::from_ref(&tmp), pageindex_config);
    let _ = fs::remove_dir_all(&tmp);
    result
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
    source: &Path,
    catalog_root: &Path,
    pageindex_config: &PageIndexConfig,
    _pipeline: &str,
    index_params_hash: &str,
    _policy: CachePolicy,
    disk_ok: bool,
    materialize_bm25: bool,
    lazy_registry: bool,
) -> Result<SkillEntryRef, String> {
    let content_sha256 = file_content_hash(source)?;
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
    } else if lazy_registry {
        lazy_pending = true;
        document = Some(stub_document_from_source(source, &doc_id, &content_sha256)?);
    } else {
        let index = build_page_index_for_source(source, pageindex_config)?;

        if disk_ok {
            let extras = SkillDocumentExtras {
                content_sha256: content_sha256.clone(),
                pipeline: String::new(),
                index_params: serde_json::Value::Null,
                built_at: String::new(),
                source_path: source.display().to_string(),
            };
            write_page_index_entry(&index, &entry_dir, &doc_id, Some(&extras))?;
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
    let root = expand_tilde(catalog_root);
    let disk_ok = policy != CachePolicy::ForceMemory && disk_available(&root);
    let materialize_bm25 = pipeline.trim().eq_ignore_ascii_case("bm25");
    let lazy_registry = memory_cache_config().lazy_registry;
    let mut refs = Vec::new();

    for source in source_paths {
        if !source.is_file() {
            continue;
        }
        refs.push(ensure_one_skill_entry(
            source,
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
