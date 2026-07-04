//! On-demand skill entry materialization for lazy registry mode.

#![allow(clippy::too_many_arguments, clippy::too_many_lines)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use super::disk_writer::maybe_enqueue_skills_index;
use super::hot::{
    get_merged_document, get_skills_index, store_merged_document, store_skills_index,
};
use super::manifest::CacheStatus;
use super::{CachePolicy, SkillEntryRef, disk_available};
use crate::pageindex::{
    EntryMetadata, PageIndexConfig, SkillDocument, build_chunk_variant, build_page_index_for_file,
    chunk_variant_valid, load_merged_document_json, page_index_valid, write_page_index_entry,
};
use crate::skills_io::refresh_skills_index_cache;

/// Extract YAML frontmatter from raw markdown (between `---` fences).
#[must_use]
pub fn extract_frontmatter_from_markdown(raw: &str) -> Option<String> {
    if !raw.starts_with("---") {
        return None;
    }
    let end = raw.find("\n---")?;
    Some(raw[3..end].trim().to_string())
}

/// Minimal document JSON for lazy registry entries (frontmatter only, no structure).
pub fn stub_document_from_source(source: &Path, doc_id: &str) -> Result<Value, String> {
    let raw = fs::read_to_string(source).map_err(|e| e.to_string())?;
    let frontmatter = extract_frontmatter_from_markdown(&raw);
    Ok(json!({
        "id": doc_id,
        "type": "md",
        "path": source.display().to_string(),
        "doc_name": doc_id,
        "line_count": 0,
        "structure": [],
        "frontmatter": frontmatter,
    }))
}

/// Ensure a skill entry is indexed in memory (and optionally on disk).
///
/// # Errors
///
/// Returns an error when the source file cannot be read or indexed.
pub fn materialize_skill_entry(
    source: &Path,
    entry_dir: &Path,
    doc_id: &str,
    content_sha256: &str,
    pageindex_config: &PageIndexConfig,
    pipeline: &str,
    index_params_hash: &str,
    policy: CachePolicy,
) -> Result<SkillEntryRef, String> {
    let materialize_bm25 = pipeline.trim().eq_ignore_ascii_case("bm25");
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

    if let Some(document) = get_merged_document(entry_dir, bm25_chunk_dir.as_deref()) {
        return Ok(materialized_ref(
            source,
            entry_dir,
            doc_id,
            content_sha256,
            bm25_chunk_dir,
            document,
            true,
            CacheStatus::Hit,
        ));
    }

    if page_index_valid(entry_dir, content_sha256) {
        let document = load_merged_document_json(entry_dir, doc_id, bm25_chunk_dir.as_deref())?;
        let _ = store_merged_document(entry_dir, bm25_chunk_dir.as_deref(), document.clone());
        if let Ok(index) = crate::skills_io::load_skills_index_from_entry(
            entry_dir,
            doc_id,
            bm25_chunk_dir.as_deref(),
        ) {
            store_skills_index(entry_dir, doc_id, bm25_chunk_dir.as_deref(), index);
        }
        return Ok(materialized_ref(
            source,
            entry_dir,
            doc_id,
            content_sha256,
            bm25_chunk_dir,
            document,
            true,
            CacheStatus::Hit,
        ));
    }

    let index = build_page_index_for_file(source, pageindex_config)?;

    let disk_ok = policy != CachePolicy::ForceMemory && disk_available(entry_dir);
    let (disk_backed, cache_status) = if disk_ok {
        maybe_enqueue_skills_index(entry_dir.to_path_buf(), index.clone());
        (true, CacheStatus::Miss)
    } else {
        (false, CacheStatus::MemoryFallback)
    };

    if disk_ok {
        let metadata = EntryMetadata {
            source_path: source.display().to_string(),
            pipeline: String::new(),
            index_params: serde_json::Value::Null,
        };
        write_page_index_entry(&index, entry_dir, doc_id, Some(&metadata))?;
    }

    if materialize_bm25 && !chunk_variant_valid(entry_dir, "bm25", index_params_hash, doc_id) {
        build_chunk_variant(
            entry_dir,
            doc_id,
            "bm25",
            index_params_hash,
            pageindex_config,
        )?;
        refresh_skills_index_cache(entry_dir, doc_id, bm25_chunk_dir.as_deref());
    }

    let document = if disk_backed {
        load_merged_document_json(entry_dir, doc_id, bm25_chunk_dir.as_deref())?
    } else {
        index.documents.get(doc_id).map_or_else(
            || stub_document_from_source(source, doc_id).unwrap_or_else(|_| json!({})),
            SkillDocument::to_json,
        )
    };
    let _ = store_merged_document(entry_dir, bm25_chunk_dir.as_deref(), document.clone());

    Ok(materialized_ref(
        source,
        entry_dir,
        doc_id,
        content_sha256,
        bm25_chunk_dir,
        document,
        disk_backed,
        cache_status,
    ))
}

fn materialized_ref(
    source: &Path,
    entry_dir: &Path,
    doc_id: &str,
    content_sha256: &str,
    bm25_chunk_dir: Option<PathBuf>,
    document: Value,
    disk_backed: bool,
    cache_status: CacheStatus,
) -> SkillEntryRef {
    SkillEntryRef {
        entry_dir: entry_dir.to_path_buf(),
        doc_id: doc_id.to_string(),
        content_sha256: content_sha256.to_string(),
        bm25_chunk_dir,
        disk_backed,
        cache_status,
        source_path: source.display().to_string(),
        nodes_dir: if disk_backed {
            Some(crate::pageindex::cache_layout::nodes_dir(entry_dir))
        } else {
            None
        },
        document: Some(document),
        lazy_pending: false,
    }
}

/// True when the entry still needs a full page/chunk index build.
#[must_use]
pub fn entry_needs_materialization(entry: &SkillEntryRef) -> bool {
    entry.lazy_pending
        || entry.document.as_ref().is_none_or(|doc| {
            doc.get("structure")
                .and_then(Value::as_array)
                .is_none_or(Vec::is_empty)
                && entry
                    .bm25_chunk_dir
                    .as_ref()
                    .is_some_and(|dir| !dir.join("chunk_index.json").is_file())
        })
}

/// Materialize when lazy registry deferred indexing for this entry.
///
/// # Errors
///
/// Returns an error when materialization fails.
pub fn ensure_entry_materialized(
    source: &Path,
    entry: &SkillEntryRef,
    pageindex_config: &PageIndexConfig,
    pipeline: &str,
    index_params_hash: &str,
    policy: CachePolicy,
) -> Result<(), String> {
    if !entry_needs_materialization(entry)
        && get_skills_index(
            &entry.entry_dir,
            &entry.doc_id,
            entry.bm25_chunk_dir.as_deref(),
        )
        .is_some()
    {
        return Ok(());
    }
    let _ = materialize_skill_entry(
        source,
        &entry.entry_dir,
        &entry.doc_id,
        &entry.content_sha256,
        pageindex_config,
        pipeline,
        index_params_hash,
        policy,
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_frontmatter_block() {
        let raw = "---\nname: demo\n---\n# Body";
        assert_eq!(
            extract_frontmatter_from_markdown(raw),
            Some("name: demo".to_string())
        );
    }
}
