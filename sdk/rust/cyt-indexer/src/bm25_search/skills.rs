//! Composite skills BM25 search (frontmatter gate + chunk search + reconstruct).

#![allow(
    clippy::too_many_arguments,
    clippy::too_many_lines,
    clippy::option_if_let_else
)]

use std::collections::{HashMap, HashSet};
use std::hash::BuildHasher;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::bm25_search::{NormalizeMode, ScoreCatalogOptions, score_catalog_dict};
use crate::cache::{
    CachePolicy, get_merged_document, materialize_skill_entry, memory_cache_config,
    read_chunk_body, store_merged_document,
};
use crate::pageindex::cache_layout::chunk_md_path;
use crate::pageindex::{PageIndexConfig, ReconstructOptions, reconstruct_skill_markdown};
use crate::pageindex::{load_merged_document_json, parse_document_on_disk};
use crate::skills_io::load_skills_index_from_entry;
use crate::tiktoken;

#[derive(Debug, Clone)]
pub struct SkillEntryInput {
    pub entry_dir: String,
    pub doc_id: String,
    pub source_path: String,
    pub frontmatter: Option<String>,
    pub cache_key: String,
    pub bm25_chunk_dir: String,
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
                bm25_chunk_dir: obj
                    .get("bm25_chunk_dir")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| {
                        obj.get("entry_dir")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .unwrap_or_default(),
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

fn index_params_hash_from_chunk_dir(chunk_dir: &Path) -> String {
    chunk_dir
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("default")
        .to_string()
}

fn ensure_entry_indexed(entry: &SkillEntryInput) -> Result<(), String> {
    let entry_path = PathBuf::from(&entry.entry_dir);
    let chunk_dir = PathBuf::from(&entry.bm25_chunk_dir);
    if chunk_dir.join("chunk_index.json").is_file() {
        return Ok(());
    }
    if !memory_cache_config().lazy_registry || entry.source_path.is_empty() {
        return Ok(());
    }
    let source = PathBuf::from(&entry.source_path);
    if !source.is_file() {
        return Ok(());
    }
    let content_sha256 = entry.cache_key.clone();
    let index_params_hash = index_params_hash_from_chunk_dir(&chunk_dir);
    let _ = materialize_skill_entry(
        &source,
        &entry_path,
        &entry.doc_id,
        &content_sha256,
        &PageIndexConfig::default(),
        "bm25",
        &index_params_hash,
        CachePolicy::Auto,
    )?;
    Ok(())
}

fn load_entry_document(entry_path: &Path, doc_id: &str, chunk_dir: &Path) -> Result<Value, String> {
    if let Some(doc) = get_merged_document(entry_path, Some(chunk_dir)) {
        return Ok(doc);
    }
    let doc = load_merged_document_json(entry_path, doc_id, Some(chunk_dir))?;
    Ok(store_merged_document(entry_path, Some(chunk_dir), doc))
}

fn build_chunk_corpus(entries: &[SkillEntryInput]) -> Result<Value, String> {
    let mut md_items = Vec::new();
    for entry in entries {
        ensure_entry_indexed(entry)?;
        let entry_path = PathBuf::from(&entry.entry_dir);
        let chunk_dir = PathBuf::from(&entry.bm25_chunk_dir);
        let Ok(doc) = load_entry_document(&entry_path, &entry.doc_id, &chunk_dir) else {
            continue;
        };
        let Some(parsed) = parse_document_on_disk(&doc) else {
            continue;
        };
        let source_path = if entry.source_path.is_empty() {
            parsed.path.clone()
        } else {
            entry.source_path.clone()
        };
        for chunk_id in iter_content_chunk_ids(&parsed.structure) {
            let chunk_id = u32::try_from(chunk_id).unwrap_or(u32::MAX);
            let chunk_path = chunk_md_path(&chunk_dir, chunk_id);
            let Ok(content) = read_chunk_body(&chunk_path) else {
                continue;
            };
            let body = strip_frontmatter(&content);
            if body.trim().is_empty() {
                continue;
            }
            md_items.push(json!({
                "id": chunk_id.to_string(),
                "doc_id": parsed.doc_id,
                "file_path": source_path,
                "content": body,
                "score": "0.0",
                "entry_dir": entry.entry_dir,
                "cache_key": entry.cache_key,
                "bm25_chunk_dir": entry.bm25_chunk_dir,
            }));
        }
    }
    Ok(json!({ "md": md_items, "json": [] }))
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
        let bm25_chunk_dir = items
            .first()
            .and_then(|i| i.get("bm25_chunk_dir"))
            .and_then(Value::as_str)
            .unwrap_or(entry_dir.as_str())
            .to_string();
        let mut chunk_ids: Vec<i64> = items
            .iter()
            .filter_map(|i| i.get("id").and_then(Value::as_str))
            .filter_map(|s| s.parse().ok())
            .collect();
        chunk_ids.sort_unstable();
        chunk_ids.dedup();

        let index = load_skills_index_from_entry(
            Path::new(&entry_dir),
            &doc_id,
            Some(Path::new(&bm25_chunk_dir)),
        )?;
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

    let corpus = build_chunk_corpus(&parsed)?;
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

const AGENT_SKILLS_INTRO: &str = "Based on the user query added chunks of descriptions of skills (not entire skill). The entire skill could be retrieved with the file path, though in most cases it likely excessive.";

fn strip_injection_frontmatter(content: &str) -> String {
    strip_frontmatter(content)
        .trim_start_matches('\n')
        .to_string()
}

fn skill_name_from_frontmatter_text(frontmatter: Option<&str>, doc_id: &str) -> String {
    frontmatter
        .and_then(|fm| {
            fm.lines()
                .find_map(|line| line.strip_prefix("name:").map(str::trim))
        })
        .unwrap_or(doc_id)
        .to_string()
}

fn format_agent_skills_injection(matches: &[Value]) -> String {
    if matches.is_empty() {
        return String::new();
    }
    let mut lines = vec![
        AGENT_SKILLS_INTRO.to_string(),
        String::new(),
        "<agent-skills>".to_string(),
    ];
    for item in matches {
        let Some(obj) = item.as_object() else {
            continue;
        };
        let file_path = obj.get("file_path").and_then(Value::as_str).unwrap_or("");
        let name = obj
            .get("name")
            .and_then(Value::as_str)
            .filter(|n| !n.is_empty());
        let markdown = obj.get("markdown").and_then(Value::as_str).unwrap_or("");
        let body = strip_injection_frontmatter(markdown);
        if body.is_empty() {
            continue;
        }
        let open = if let Some(name) = name {
            format!(r#"<skill name="{name}" path="{file_path}">"#)
        } else {
            format!(r#"<skill path="{file_path}">"#)
        };
        lines.push(open);
        lines.push(body);
        lines.push("</skill>".to_string());
    }
    lines.push("</agent-skills>".to_string());
    lines.join("\n")
}

fn wrapped_injection_tokens(matches: &[Value]) -> usize {
    let injected = format_agent_skills_injection(matches);
    if injected.is_empty() {
        return 0;
    }
    tiktoken::count_tokens_or_min(&injected)
}

fn reconstruct_group_match(
    entry_dir: &str,
    doc_id: &str,
    bm25_chunk_dir: &str,
    item_kind: &str,
    id_specs: &[String],
    file_path: &str,
    top_score: f64,
    frontmatter: Option<&str>,
) -> Result<Option<Value>, String> {
    let chunk_dir = if bm25_chunk_dir.is_empty() {
        None
    } else {
        Some(Path::new(bm25_chunk_dir))
    };
    let index = load_skills_index_from_entry(Path::new(entry_dir), doc_id, chunk_dir)?;
    let spec_refs: Vec<&str> = id_specs.iter().map(String::as_str).collect();
    let reconstructed = if item_kind == "chunk" {
        reconstruct_skill_markdown(
            &index,
            doc_id,
            &[],
            &[],
            &spec_refs,
            &ReconstructOptions::default(),
        )?
    } else {
        reconstruct_skill_markdown(
            &index,
            doc_id,
            &[],
            &spec_refs,
            &[],
            &ReconstructOptions::default(),
        )?
    };
    let markdown = reconstructed.markdown.trim().to_string();
    if markdown.is_empty() {
        return Ok(None);
    }
    Ok(Some(json!({
        "doc_id": doc_id,
        "file_path": file_path,
        "markdown": markdown,
        "name": skill_name_from_frontmatter_text(frontmatter, doc_id),
        "score": top_score,
        "token_count": tiktoken::count_tokens_or_min(&markdown),
        "entry_dir": entry_dir,
    })))
}

/// Reconstruct multiple doc groups in one call.
///
/// # Errors
///
/// Returns an error when index loading or reconstruction fails.
pub fn batch_reconstruct_skill_matches(groups: &[Value]) -> Result<Vec<Value>, String> {
    let mut matches = Vec::new();
    for group in groups {
        let Some(obj) = group.as_object() else {
            continue;
        };
        let entry_dir = obj.get("entry_dir").and_then(Value::as_str).unwrap_or("");
        let doc_id = obj.get("doc_id").and_then(Value::as_str).unwrap_or("");
        if entry_dir.is_empty() || doc_id.is_empty() {
            continue;
        }
        let bm25_chunk_dir = obj
            .get("bm25_chunk_dir")
            .and_then(Value::as_str)
            .unwrap_or(entry_dir);
        let item_kind = obj
            .get("item_kind")
            .and_then(Value::as_str)
            .unwrap_or("node");
        let file_path = obj.get("file_path").and_then(Value::as_str).unwrap_or("");
        let top_score = obj.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let frontmatter = obj.get("frontmatter").and_then(Value::as_str);
        let id_specs: Vec<String> = obj
            .get("id_specs")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        if let Some(item) = reconstruct_group_match(
            entry_dir,
            doc_id,
            bm25_chunk_dir,
            item_kind,
            &id_specs,
            file_path,
            top_score,
            frontmatter,
        )? {
            matches.push(item);
        }
    }
    matches.sort_by(|a, b| {
        b.get("score")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            .partial_cmp(&a.get("score").and_then(Value::as_f64).unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(matches)
}

/// Greedy budget selection over search survivors with reconstruct + wrapped token counting.
///
/// # Errors
///
/// Returns an error when reconstruction fails.
pub fn greedy_select_skill_items(
    survivors: &[Value],
    item_kind: &str,
    max_tokens: i64,
) -> Result<Value, String> {
    let mut best_by_key: HashMap<(String, String), Value> = HashMap::new();
    for row in survivors {
        let Some(obj) = row.as_object() else {
            continue;
        };
        if !obj.get("passed").and_then(Value::as_bool).unwrap_or(false) {
            continue;
        }
        let file_path = obj
            .get("file_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let item_id = obj
            .get("item_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if file_path.is_empty() || item_id.is_empty() {
            continue;
        }
        let score = obj.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let key = (file_path.clone(), item_id.clone());
        let replace = best_by_key
            .get(&key)
            .and_then(Value::as_object)
            .and_then(|existing| existing.get("score"))
            .and_then(Value::as_f64)
            .is_none_or(|existing_score| score > existing_score);
        if replace {
            best_by_key.insert(key, row.clone());
        }
    }

    let mut ordered: Vec<Value> = best_by_key.into_values().collect();
    ordered.sort_by(|a, b| {
        let score_a = a.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let score_b = b.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        score_b
            .partial_cmp(&score_a)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                a.get("file_path")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("file_path").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| {
                a.get("item_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("item_id").and_then(Value::as_str).unwrap_or(""))
            })
    });

    let mut selected: Vec<Value> = Vec::new();
    let mut best_matches: Vec<Value> = Vec::new();
    let mut rows_by_doc: HashMap<(String, String), Vec<Value>> = HashMap::new();
    let mut match_by_doc: HashMap<(String, String), Option<Value>> = HashMap::new();
    let mut budget_trace: Vec<Value> = Vec::new();

    for row in ordered {
        let Some(obj) = row.as_object() else {
            continue;
        };
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
        let file_path = obj
            .get("file_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let item_id = obj
            .get("item_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let score = obj.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let bm25_chunk_dir = obj
            .get("bm25_chunk_dir")
            .and_then(Value::as_str)
            .unwrap_or(entry_dir.as_str())
            .to_string();
        let frontmatter = obj.get("frontmatter").and_then(Value::as_str);

        let doc_key = (entry_dir.clone(), doc_id.clone());
        let mut trial_doc_rows = rows_by_doc.get(&doc_key).cloned().unwrap_or_default();
        trial_doc_rows.push(row.clone());
        let mut id_specs: Vec<String> = trial_doc_rows
            .iter()
            .filter_map(|item| item.get("item_id").and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        id_specs.sort_by(|a, b| {
            a.parse::<i64>()
                .unwrap_or(0)
                .cmp(&b.parse::<i64>().unwrap_or(0))
        });
        id_specs.dedup();
        let trial_doc_match = reconstruct_group_match(
            &entry_dir,
            &doc_id,
            &bm25_chunk_dir,
            item_kind,
            &id_specs,
            &file_path,
            trial_doc_rows
                .iter()
                .filter_map(|item| item.get("score").and_then(Value::as_f64))
                .fold(0.0_f64, f64::max),
            frontmatter,
        )?;

        let mut trial_match_by_doc = match_by_doc.clone();
        trial_match_by_doc.insert(doc_key.clone(), trial_doc_match.clone());
        let mut trial_matches: Vec<Value> = trial_match_by_doc
            .values()
            .filter_map(std::clone::Clone::clone)
            .collect();
        trial_matches.sort_by(|a, b| {
            b.get("score")
                .and_then(Value::as_f64)
                .unwrap_or(0.0)
                .partial_cmp(&a.get("score").and_then(Value::as_f64).unwrap_or(0.0))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let tokens = i64::try_from(wrapped_injection_tokens(&trial_matches)).unwrap_or(i64::MAX);
        let passed = tokens <= max_tokens;
        budget_trace.push(json!({
            "file_path": file_path,
            "item_id": item_id,
            "item_kind": item_kind,
            "score": score,
            "tokens": tokens,
            "passed": passed,
        }));
        if passed {
            selected.push(row);
            rows_by_doc.insert(doc_key.clone(), trial_doc_rows);
            match_by_doc.insert(doc_key, trial_doc_match.clone());
            best_matches = trial_matches;
        }
    }

    Ok(json!({
        "matches": best_matches,
        "selected": selected,
        "budget_trace": budget_trace,
    }))
}
