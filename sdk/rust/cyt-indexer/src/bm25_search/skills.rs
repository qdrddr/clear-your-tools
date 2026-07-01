//! Composite skills BM25 search (frontmatter gate + chunk search + reconstruct).

use std::collections::{HashMap, HashSet};
use std::fs;
use std::hash::BuildHasher;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::bm25_search::{NormalizeMode, ScoreCatalogOptions, score_catalog_dict};
use crate::pageindex::{ReconstructOptions, reconstruct_skill_markdown};
use crate::skills_io::load_skills_index_from_dir;
use crate::tiktoken;

#[derive(Debug, Clone)]
pub struct SkillEntryInput {
    pub entry_dir: String,
    pub doc_id: String,
    pub source_path: String,
    pub frontmatter: Option<String>,
    pub cache_key: String,
}

fn parse_entries(entries: &[Value]) -> Vec<SkillEntryInput> {
    entries
        .iter()
        .filter_map(|entry| {
            let obj = entry.as_object()?;
            Some(SkillEntryInput {
                entry_dir: obj.get("entry_dir")?.as_str()?.to_string(),
                doc_id: obj.get("doc_id")?.as_str()?.to_string(),
                source_path: obj
                    .get("source_path")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                frontmatter: obj
                    .get("frontmatter")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                cache_key: obj
                    .get("cache_key")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            })
        })
        .collect()
}

fn strip_frontmatter(content: &str) -> String {
    if !content.starts_with("---") {
        return content.to_string();
    }
    if let Some(end) = content.find("\n---") {
        let body_start = end + 4;
        return content[body_start..].trim_start_matches('\n').to_string();
    }
    content.to_string()
}

fn is_frontmatter_node(node: &Value) -> bool {
    node.get("kind")
        .and_then(Value::as_str)
        .is_some_and(|k| k == "frontmatter")
}

fn iter_content_chunk_ids(structure: &Value) -> Vec<i64> {
    let mut ids = Vec::new();
    if let Some(arr) = structure.as_array() {
        for node in arr {
            walk_structure(node, &mut ids, true);
        }
    } else {
        walk_structure(structure, &mut ids, true);
    }
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn walk_structure(node: &Value, out: &mut Vec<i64>, skip_frontmatter: bool) {
    let Some(obj) = node.as_object() else {
        return;
    };
    if skip_frontmatter && is_frontmatter_node(node) {
        // Still walk children if any, but skip this node's chunks.
    } else if let Some(chunks) = obj.get("chunks").and_then(Value::as_array) {
        for chunk in chunks {
            if let Some(id) = chunk.get("chunk_id").and_then(Value::as_i64) {
                out.push(id);
            }
        }
    }
    if let Some(children) = obj.get("nodes").and_then(Value::as_array) {
        for child in children {
            walk_structure(child, out, skip_frontmatter);
        }
    }
}

fn build_chunk_corpus(entries: &[SkillEntryInput]) -> Value {
    let mut md_items = Vec::new();
    for entry in entries {
        let doc_dir = PathBuf::from(&entry.entry_dir)
            .join("skills")
            .join("decomposed")
            .join(&entry.doc_id);
        let doc_json_path = doc_dir.join("document.json");
        let Ok(raw) = fs::read_to_string(&doc_json_path) else {
            continue;
        };
        let Ok(doc) = serde_json::from_str::<Value>(&raw) else {
            continue;
        };
        let Some(structure) = doc.get("structure") else {
            continue;
        };
        for chunk_id in iter_content_chunk_ids(structure) {
            let chunk_path = doc_dir.join("chunks").join(format!("{chunk_id}.md"));
            let Ok(content) = fs::read_to_string(&chunk_path) else {
                continue;
            };
            let body = strip_frontmatter(&content);
            if body.trim().is_empty() {
                continue;
            }
            md_items.push(json!({
                "id": chunk_id.to_string(),
                "doc_id": entry.doc_id,
                "file_path": entry.source_path,
                "content": body,
                "score": "0.0",
                "entry_dir": entry.entry_dir,
                "cache_key": entry.cache_key,
            }));
        }
    }
    json!({ "md": md_items, "json": [] })
}

/// Frontmatter gate: return excluded `(entry_dir, doc_id)` pairs with high similarity.
///
/// # Errors
///
/// Returns an error when catalog scoring fails.
pub fn bm25_frontmatter_gate(
    entries: &[Value],
    query: &str,
    upper_limit: f64,
) -> Result<(HashSet<(String, String)>, Value), String> {
    let parsed = parse_entries(entries);
    let mut md_items = Vec::new();
    for entry in &parsed {
        let fm = entry.frontmatter.as_deref().unwrap_or("").trim();
        if fm.is_empty() {
            continue;
        }
        md_items.push(json!({
            "id": entry.doc_id,
            "doc_id": entry.doc_id,
            "file_path": entry.source_path,
            "content": fm,
            "score": "0.0",
            "entry_dir": entry.entry_dir,
        }));
    }
    let corpus = json!({ "md": md_items, "json": [] });
    let options = ScoreCatalogOptions {
        json_normalize: NormalizeMode::ExpSimilarity,
        md_normalize: NormalizeMode::ExpSimilarity,
        prune_enums: false,
        ..ScoreCatalogOptions::default()
    };
    let scored = score_catalog_dict(corpus, query, &options)?;

    let mut excluded = HashSet::new();
    let mut trace_rows = Vec::new();
    if let Some(items) = scored.get("md").and_then(Value::as_array) {
        for item in items {
            let Some(obj) = item.as_object() else {
                continue;
            };
            let score = obj
                .get("score")
                .and_then(Value::as_str)
                .and_then(|s| s.parse::<f64>().ok())
                .unwrap_or(0.0);
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
            let gate_passed = score < upper_limit;
            trace_rows.push(json!({
                "entry_dir": entry_dir,
                "doc_id": doc_id,
                "score": score,
                "passed": gate_passed,
            }));
            if !gate_passed {
                excluded.insert((entry_dir, doc_id));
            }
        }
    }
    Ok((
        excluded,
        json!({ "rows": trace_rows, "upper_limit": upper_limit }),
    ))
}

fn collect_scored_survivors(scored: &Value, threshold: f64) -> (Vec<Value>, Vec<Value>) {
    let mut survivors = Vec::new();
    let mut trace_rows = Vec::new();
    if let Some(items) = scored.get("md").and_then(Value::as_array) {
        for item in items {
            let Some(obj) = item.as_object() else {
                continue;
            };
            let score = obj
                .get("score")
                .and_then(Value::as_str)
                .and_then(|s| s.parse::<f64>().ok())
                .unwrap_or(0.0);
            let meets_threshold = score >= threshold;
            trace_rows.push(json!({
                "doc_id": obj.get("doc_id"),
                "item_id": obj.get("id"),
                "score": score,
                "passed": meets_threshold,
                "file_path": obj.get("file_path"),
            }));
            if meets_threshold {
                survivors.push(item.clone());
            }
        }
    }
    (survivors, trace_rows)
}

fn group_survivors_by_doc(survivors: Vec<Value>) -> HashMap<String, Vec<Value>> {
    let mut by_doc: HashMap<String, Vec<Value>> = HashMap::new();
    for item in survivors {
        let doc_id = item
            .get("doc_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        by_doc.entry(doc_id).or_default().push(item);
    }
    by_doc
}

fn rebuild_skill_matches(
    by_doc: HashMap<String, Vec<Value>>,
    frontmatter_by_doc: &HashMap<(String, String), Option<String>>,
) -> Result<Vec<Value>, String> {
    let mut matches = Vec::new();
    for (doc_id, items) in by_doc {
        let entry_dir = items
            .first()
            .and_then(|i| i.get("entry_dir"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let mut chunk_ids: Vec<i64> = items
            .iter()
            .filter_map(|i| i.get("id").and_then(Value::as_str))
            .filter_map(|s| s.parse().ok())
            .collect();
        chunk_ids.sort_unstable();
        chunk_ids.dedup();

        let index = load_skills_index_from_dir(Path::new(&entry_dir))?;
        let specs: Vec<String> = chunk_ids
            .iter()
            .map(std::string::ToString::to_string)
            .collect();
        let spec_refs: Vec<&str> = specs.iter().map(String::as_str).collect();
        let reconstructed = reconstruct_skill_markdown(
            &index,
            &doc_id,
            &[],
            &[],
            &spec_refs,
            &ReconstructOptions::default(),
        )?;
        let markdown = reconstructed.markdown;
        if markdown.trim().is_empty() {
            continue;
        }
        let top_score = items
            .iter()
            .filter_map(|i| {
                i.get("score")
                    .and_then(Value::as_str)
                    .and_then(|s| s.parse::<f64>().ok())
            })
            .fold(0.0_f64, f64::max);
        let file_path = items
            .first()
            .and_then(|i| i.get("file_path"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let name = frontmatter_by_doc
            .get(&(entry_dir.clone(), doc_id.clone()))
            .and_then(|fm| fm.as_deref())
            .and_then(|fm| {
                fm.lines()
                    .find_map(|line| line.strip_prefix("name:").map(str::trim))
            })
            .unwrap_or(&doc_id)
            .to_string();
        let token_count = tiktoken::count_tokens_or_min(&markdown);
        matches.push(json!({
            "doc_id": doc_id,
            "file_path": file_path,
            "markdown": markdown,
            "name": name,
            "score": top_score,
            "token_count": token_count,
            "entry_dir": entry_dir,
        }));
    }
    Ok(matches)
}

/// Search skill chunks, reconstruct matched skills, return matches + trace.
///
/// # Errors
///
/// Returns an error when catalog scoring, index loading, or reconstruction fails.
pub fn bm25_search_skill_chunks<S: BuildHasher>(
    entries: &[Value],
    query: &str,
    threshold: f64,
    excluded: &HashSet<(String, String), S>,
) -> Result<Value, String> {
    let parsed: Vec<SkillEntryInput> = parse_entries(entries)
        .into_iter()
        .filter(|e| !excluded.contains(&(e.entry_dir.clone(), e.doc_id.clone())))
        .collect();

    let corpus = build_chunk_corpus(&parsed);
    let options = ScoreCatalogOptions {
        prune_enums: false,
        ..ScoreCatalogOptions::default()
    };
    let scored = score_catalog_dict(corpus, query, &options)?;

    let (survivors, trace_rows) = collect_scored_survivors(&scored, threshold);
    let by_doc = group_survivors_by_doc(survivors);
    let frontmatter_by_doc: HashMap<(String, String), Option<String>> = parsed
        .iter()
        .map(|e| {
            (
                (e.entry_dir.clone(), e.doc_id.clone()),
                e.frontmatter.clone(),
            )
        })
        .collect();
    let matches = rebuild_skill_matches(by_doc, &frontmatter_by_doc)?;

    Ok(json!({
        "matches": matches,
        "trace_rows": trace_rows,
        "threshold": threshold,
    }))
}
