// N-API bindings for Tantivy BM25 catalog search (included from `node.rs`).

use crate::bm25_search::{
    self, bm25_frontmatter_gate, bm25_search_skill_chunks, collect_catalog_documents,
    score_catalog_in_place, ScoreCatalogOptions,
};
use serde_json::json;

fn excluded_set_from_value(excluded: Option<Value>) -> HashSet<(String, String)> {
    let mut set = HashSet::new();
    if let Some(items) = excluded.and_then(|v| v.as_array().cloned()) {
        for item in items {
            if let Some(obj) = item.as_object() {
                let entry_dir = obj
                    .get("entry_dir")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let doc_id = obj
                    .get("doc_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                set.insert((entry_dir, doc_id));
            }
        }
    }
    set
}

/// Override BM25 search defaults in the native core.
#[napi(js_name = "configureBm25Defaults")]
pub fn configure_bm25_defaults_napi(
    index_dir: Option<String>,
    stem_language: Option<String>,
    stopwords: Option<String>,
    use_stopwords: Option<bool>,
    k1: Option<f64>,
    b: Option<f64>,
    mmap: Option<bool>,
) {
    let mut cfg = bm25_search::snapshot();
    if let Some(dir) = index_dir {
        cfg.index_dir = dir.into();
    }
    if let Some(v) = stem_language {
        cfg.stem_language = v;
    }
    if let Some(v) = stopwords {
        cfg.stopwords = v;
    }
    if let Some(v) = use_stopwords {
        cfg.use_stopwords = v;
    }
    if let Some(v) = k1 {
        cfg.k1 = v;
    }
    if let Some(v) = b {
        cfg.b = v;
    }
    if let Some(v) = mmap {
        cfg.mmap = v;
    }
    bm25_search::configure(&cfg);
}

/// Hash catalog documents plus analyzer settings.
///
/// # Errors
///
/// This function currently does not fail; the `Result` type is reserved for future validation.
#[napi(js_name = "bm25CatalogFingerprint")]
pub fn bm25_catalog_fingerprint_napi(data: Value) -> Result<String> {
    let data = Box::new(data);
    let cfg = bm25_search::snapshot();
    let docs = collect_catalog_documents(&data);
    Ok(bm25_search::catalog_fingerprint(
        &docs,
        &cfg.stem_language,
        &cfg.stopwords,
    ))
}

/// Score catalog json/md lists in-place and return the updated catalog dict.
///
/// # Errors
///
/// Returns an error when catalog scoring fails.
#[napi(js_name = "bm25ScoreCatalog")]
pub fn bm25_score_catalog_napi(
    data: Value,
    query: String,
    prune_json_threshold: Option<f64>,
    prune_md_threshold: Option<f64>,
    prune_enums: Option<bool>,
) -> Result<Value> {
    let mut data = Box::new(data);
    let query = query.into_boxed_str();
    let options = ScoreCatalogOptions {
        prune_json_threshold,
        prune_md_threshold,
        prune_enums: prune_enums.unwrap_or(true),
        ..ScoreCatalogOptions::default()
    };
    score_catalog_in_place(&mut data, query.as_ref(), &options).map_err(Error::from_reason)?;
    Ok(*data)
}

/// Return excluded entry refs and trace metadata for frontmatter gating.
///
/// # Errors
///
/// Returns an error when frontmatter scoring fails.
#[napi(js_name = "bm25FrontmatterGate")]
pub fn bm25_frontmatter_gate_napi(
    entries: Value,
    query: String,
    upper_limit: Option<f64>,
) -> Result<Value> {
    let entries = Box::new(entries);
    let query = query.into_boxed_str();
    let arr = entries.as_array().cloned().unwrap_or_default();
    let (excluded, trace) = bm25_frontmatter_gate(
        &arr,
        query.as_ref(),
        upper_limit.unwrap_or(0.4),
    )
    .map_err(Error::from_reason)?;
    let excluded_json: Vec<Value> = excluded
        .into_iter()
        .map(|(entry_dir, doc_id)| json!({ "entry_dir": entry_dir, "doc_id": doc_id }))
        .collect();
    Ok(json!({ "excluded": excluded_json, "trace": trace }))
}

/// Search skill chunks, reconstruct matches, return matches + trace.
///
/// # Errors
///
/// Returns an error when chunk search or reconstruction fails.
#[napi(js_name = "bm25SearchSkillChunks")]
pub fn bm25_search_skill_chunks_napi(
    entries: Value,
    query: String,
    threshold: Option<f64>,
    excluded: Option<Value>,
) -> Result<Value> {
    let entries = Box::new(entries);
    let query = query.into_boxed_str();
    let arr = entries.as_array().cloned().unwrap_or_default();
    let excluded_set = excluded_set_from_value(excluded);
    bm25_search_skill_chunks(
        &arr,
        query.as_ref(),
        threshold.unwrap_or(0.5),
        &excluded_set,
    )
    .map_err(Error::from_reason)
}
