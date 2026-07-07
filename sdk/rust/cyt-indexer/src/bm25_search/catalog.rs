use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::PathBuf;

use serde_json::Value;

use super::config::{self, expand_index_dir};
use super::normalize::{NormalizeMode, normalize_scores};
use super::tantivy_score;

const FINGERPRINT_VERSION: &str = "v3-unified-analyzer";

#[derive(Debug, Clone)]
pub struct CatalogDocument {
    pub list_key: String,
    pub item_index: usize,
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct ScoreCatalogOptions {
    pub prune_json_threshold: Option<f64>,
    pub prune_md_threshold: Option<f64>,
    pub prune_enums: bool,
    pub json_normalize: NormalizeMode,
    pub md_normalize: NormalizeMode,
}

impl Default for ScoreCatalogOptions {
    fn default() -> Self {
        Self {
            prune_json_threshold: None,
            prune_md_threshold: None,
            prune_enums: true,
            json_normalize: NormalizeMode::MinMax,
            md_normalize: NormalizeMode::MinMax,
        }
    }
}

#[must_use]
pub fn catalog_fingerprint(
    docs: &[CatalogDocument],
    stem_language: &str,
    stopwords: &str,
) -> String {
    use sha2::{Digest, Sha256};
    let cfg = config::snapshot();
    let mut hasher = Sha256::new();
    hasher.update(FINGERPRINT_VERSION.as_bytes());
    hasher.update(b"\0");
    hasher.update(stem_language.as_bytes());
    hasher.update(b"\0");
    hasher.update(stopwords.as_bytes());
    hasher.update(b"\0");
    hasher.update([u8::from(cfg.use_stopwords)]);
    hasher.update(b"\0");
    hasher.update(cfg.k1.to_le_bytes());
    hasher.update(b"\0");
    hasher.update(cfg.b.to_le_bytes());
    hasher.update(b"\0");
    let mut sorted = docs.to_vec();
    sorted.sort_by(|a, b| {
        (&a.list_key, a.item_index, &a.text).cmp(&(&b.list_key, b.item_index, &b.text))
    });
    for doc in &sorted {
        hasher.update(doc.list_key.as_bytes());
        hasher.update(b"\0");
        hasher.update(doc.text.as_bytes());
        hasher.update(b"\0");
    }
    hex::encode(hasher.finalize())
}

fn index_dir_for_fingerprint(fingerprint: &str) -> PathBuf {
    let cfg = config::snapshot();
    expand_index_dir(&cfg.index_dir).join(fingerprint)
}

/// Collect indexable documents from a catalog dict `{json, md}`.
pub fn collect_catalog_documents(data: &Value) -> Vec<CatalogDocument> {
    let mut docs = Vec::new();
    for list_key in ["json", "md"] {
        let Some(items) = data.get(list_key).and_then(Value::as_array) else {
            continue;
        };
        for (item_index, item) in items.iter().enumerate() {
            let Some(obj) = item.as_object() else {
                continue;
            };
            let text = if list_key == "json" {
                obj.get("content")
                    .map(|c| {
                        if c.is_string() {
                            c.as_str().unwrap_or("").to_string()
                        } else {
                            c.to_string()
                        }
                    })
                    .unwrap_or_default()
            } else {
                obj.get("content")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string()
            };
            if text.is_empty() {
                continue;
            }
            docs.push(CatalogDocument {
                list_key: list_key.to_string(),
                item_index,
                text,
            });
        }
    }
    docs
}

/// Score catalog items in-place; optionally prune below thresholds.
///
/// # Errors
///
/// Returns an error when BM25 scoring fails.
pub fn score_catalog_in_place(
    data: &mut Value,
    query: &str,
    options: &ScoreCatalogOptions,
) -> Result<(), String> {
    let docs = collect_catalog_documents(data);
    if docs.is_empty() {
        return Ok(());
    }

    let corpus: Vec<&str> = docs.iter().map(|d| d.text.as_str()).collect();
    let cfg = super::config::snapshot();
    let fp = catalog_fingerprint(&docs, &cfg.stem_language, &cfg.stopwords);
    let raw = tantivy_score::score_corpus_cached(query, &corpus, &fp)?;

    let mut by_list: std::collections::HashMap<&str, Vec<(usize, f64)>> =
        std::collections::HashMap::new();
    for (doc_idx, doc) in docs.iter().enumerate() {
        let score = raw.get(doc_idx).copied().unwrap_or(0.0);
        by_list
            .entry(doc.list_key.as_str())
            .or_default()
            .push((doc.item_index, score));
    }

    for (list_key, entries) in by_list {
        let raws: Vec<f64> = entries.iter().map(|(_, s)| *s).collect();
        let mode = if list_key == "json" {
            options.json_normalize
        } else {
            options.md_normalize
        };
        let normalized = normalize_scores(&raws, mode);
        let items = data
            .get_mut(list_key)
            .and_then(Value::as_array_mut)
            .ok_or_else(|| format!("missing list {list_key}"))?;
        for ((item_index, _), norm) in entries.iter().zip(normalized) {
            if *item_index >= items.len() {
                continue;
            }
            if let Some(item) = items.get_mut(*item_index).and_then(Value::as_object_mut) {
                item.insert("score".to_string(), Value::String(format!("{norm:.20}")));
            }
        }
    }

    for list_key in ["json", "md"] {
        if let Some(items) = data.get_mut(list_key).and_then(Value::as_array_mut) {
            items.sort_by(|a, b| {
                let sa = a
                    .get("score")
                    .and_then(Value::as_str)
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(0.0);
                let sb = b
                    .get("score")
                    .and_then(Value::as_str)
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(0.0);
                sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
            });
        }
    }

    if let Some(threshold) = options.prune_json_threshold {
        prune_list(data, "json", threshold);
    }
    if options.prune_enums
        && let Some(threshold) = options.prune_md_threshold
    {
        prune_list(data, "md", threshold);
    }

    Ok(())
}

fn prune_list(data: &mut Value, list_key: &str, threshold: f64) {
    let Some(items) = data.get_mut(list_key).and_then(Value::as_array_mut) else {
        return;
    };
    items.retain(|item| {
        item.get("score")
            .and_then(Value::as_str)
            .and_then(|s| s.parse::<f64>().ok())
            .is_some_and(|s| s >= threshold)
    });
}

/// Score catalog json/md lists and return the updated catalog value.
///
/// # Errors
///
/// Returns an error when catalog scoring fails.
pub fn score_catalog_dict(
    mut data: Value,
    query: &str,
    options: &ScoreCatalogOptions,
) -> Result<Value, String> {
    score_catalog_in_place(&mut data, query, options)?;
    Ok(data)
}

#[must_use]
pub fn index_path_for_catalog(data: &Value) -> PathBuf {
    let cfg = config::snapshot();
    let docs = collect_catalog_documents(data);
    let fp = catalog_fingerprint(&docs, &cfg.stem_language, &cfg.stopwords);
    index_dir_for_fingerprint(&fp)
}

fn legacy_hash_u64(parts: &[&str]) -> u64 {
    let mut h = DefaultHasher::new();
    for p in parts {
        p.hash(&mut h);
    }
    h.finish()
}

#[allow(dead_code)]
pub fn legacy_hash_id(data: &Value) -> u64 {
    let docs = collect_catalog_documents(data);
    let joined: Vec<String> = docs.iter().map(|d| d.text.clone()).collect();
    legacy_hash_u64(&joined.iter().map(String::as_str).collect::<Vec<_>>())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn scores_catalog_items() -> Result<(), String> {
        let mut data = json!({
            "json": [
                {"content": "alpha beta tool", "score": "0"},
                {"content": "unrelated finance news", "score": "0"}
            ],
            "md": []
        });
        score_catalog_in_place(&mut data, "alpha beta", &ScoreCatalogOptions::default())?;
        let first = data["json"][0]["score"]
            .as_str()
            .ok_or_else(|| "missing first score".to_string())?;
        let second = data["json"][1]["score"]
            .as_str()
            .ok_or_else(|| "missing second score".to_string())?;
        let first_score = first.parse::<f64>().map_err(|err| err.to_string())?;
        let second_score = second.parse::<f64>().map_err(|err| err.to_string())?;
        assert!(first_score > second_score);
        Ok(())
    }
}
