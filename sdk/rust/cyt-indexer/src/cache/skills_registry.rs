//! BM25 skills registry ensure/build/load under ``entries/{content_sha256}/``.

use std::fs;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::pageindex::cache_layout::skill_entry_dir;
use crate::pageindex::{
    PageIndexConfig, SkillDocumentExtras, SkillsIndex, build_chunk_variant, build_page_index_only,
    chunk_variant_valid, page_index_valid, write_page_index_entry,
};
use crate::skills_io::write_skills_index;

use super::manifest::CacheStatus;
use super::{CachePolicy, disk_available, expand_tilde};

#[derive(Debug, Clone)]
pub struct SkillEntryRef {
    pub entry_dir: PathBuf,
    pub doc_id: String,
    pub content_sha256: String,
    pub bm25_chunk_dir: Option<PathBuf>,
    pub disk_backed: bool,
    pub cache_status: CacheStatus,
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

/// Ensure page index + optional BM25 chunk variant for each source skill file.
///
/// Page index and BM25 chunks are built in Rust; rerank/LLM chunk variants remain in Python.
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
    let mut refs = Vec::new();

    for source in source_paths {
        if !source.is_file() {
            continue;
        }
        let content_sha256 = file_content_hash(source)?;
        let doc_id = doc_id_from_path(source);
        let entry_dir = skill_entry_dir(&root, &content_sha256);
        let mut disk_backed = false;
        let mut cache_status = CacheStatus::MemoryFallback;

        if disk_ok {
            if page_index_valid(&entry_dir, &content_sha256) {
                cache_status = CacheStatus::Hit;
            } else {
                let index = build_page_index_for_source(source, pageindex_config)?;
                write_skills_index(&index, &entry_dir)?;
                let extras = SkillDocumentExtras {
                    content_sha256: content_sha256.clone(),
                    pipeline: String::new(),
                    index_params: serde_json::Value::Null,
                    built_at: String::new(),
                    source_path: source.display().to_string(),
                };
                write_page_index_entry(&index, &entry_dir, &doc_id, Some(&extras))?;
                cache_status = CacheStatus::Miss;
            }
            disk_backed = true;

            if materialize_bm25
                && !chunk_variant_valid(&entry_dir, "bm25", index_params_hash, &doc_id)
            {
                build_chunk_variant(
                    &entry_dir,
                    &doc_id,
                    "bm25",
                    index_params_hash,
                    pageindex_config,
                )?;
                cache_status = CacheStatus::Miss;
            }
        }

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

        refs.push(SkillEntryRef {
            entry_dir,
            doc_id,
            content_sha256,
            bm25_chunk_dir,
            disk_backed,
            cache_status,
        });
    }

    Ok(refs)
}
