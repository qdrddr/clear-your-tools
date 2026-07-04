//! Composite skills BM25 search + reconstruct + budget selection.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::bm25_search::{
    bm25_frontmatter_gate, bm25_search_skill_chunks, greedy_select_skill_items,
};
use crate::pageindex::node_id::node_id_from_value;
use crate::pageindex::{get_line_content, is_frontmatter_node, load_merged_document_json};
use crate::skills_io::load_skills_index_from_entry;

#[derive(Debug, Clone)]
pub struct SearchSkillsOptions {
    pub threshold: f64,
    pub max_tokens: i64,
    pub frontmatter_upper_limit: Option<f64>,
    pub item_kind: String,
}

impl Default for SearchSkillsOptions {
    fn default() -> Self {
        Self {
            threshold: 0.5,
            max_tokens: 0,
            frontmatter_upper_limit: None,
            item_kind: "chunk".to_string(),
        }
    }
}

fn entry_lookup(entries: &[Value]) -> HashMap<(String, String), Value> {
    let mut map = HashMap::new();
    for entry in entries {
        let Some(obj) = entry.as_object() else {
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
        if !entry_dir.is_empty() && !doc_id.is_empty() {
            map.insert((entry_dir, doc_id), entry.clone());
        }
    }
    map
}

fn enrich_survivor(row: &Value, entry: &Value) -> Value {
    let Some(row_obj) = row.as_object() else {
        return row.clone();
    };
    let Some(entry_obj) = entry.as_object() else {
        return row.clone();
    };
    let mut merged = row_obj.clone();
    for key in [
        "entry_dir",
        "doc_id",
        "bm25_chunk_dir",
        "cache_key",
        "source_path",
    ] {
        if !merged.contains_key(key)
            && let Some(v) = entry_obj.get(key)
        {
            merged.insert(key.to_string(), v.clone());
        }
    }
    if !merged.contains_key("frontmatter") {
        let frontmatter = entry_obj
            .get("frontmatter")
            .or_else(|| entry_obj.get("document").and_then(|d| d.get("frontmatter")))
            .cloned()
            .unwrap_or(Value::Null);
        merged.insert("frontmatter".into(), frontmatter);
    }
    if !merged.contains_key("bm25_chunk_dir") {
        let entry_dir = merged
            .get("entry_dir")
            .and_then(Value::as_str)
            .unwrap_or("");
        merged.insert(
            "bm25_chunk_dir".into(),
            Value::String(entry_dir.to_string()),
        );
    }
    Value::Object(merged)
}

fn iter_content_node_ids(structure: &Value) -> Vec<u32> {
    let mut ids = Vec::new();
    walk_content_nodes(structure, &mut ids, true);
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn walk_content_nodes(node: &Value, out: &mut Vec<u32>, skip_frontmatter: bool) {
    if let Some(arr) = node.as_array() {
        for child in arr {
            walk_content_nodes(child, out, skip_frontmatter);
        }
        return;
    }
    let Some(obj) = node.as_object() else {
        return;
    };
    if !(skip_frontmatter && is_frontmatter_node(obj)) {
        let node_id = node_id_from_value(obj.get("node_id"));
        if node_id > 0 {
            out.push(node_id);
        }
    }
    if let Some(children) = obj.get("nodes").and_then(Value::as_array) {
        for child in children {
            walk_content_nodes(child, out, skip_frontmatter);
        }
    }
}

fn entry_structure(entry: &Value) -> Option<Value> {
    let obj = entry.as_object()?;
    if let Some(doc) = obj.get("document").and_then(Value::as_object)
        && let Some(structure) = doc.get("structure")
    {
        return Some(structure.clone());
    }
    obj.get("structure").cloned()
}

fn load_structure_for_entry(entry: &Value) -> Result<Option<Value>, String> {
    if let Some(structure) = entry_structure(entry) {
        return Ok(Some(structure));
    }
    let Some(obj) = entry.as_object() else {
        return Ok(None);
    };
    let entry_dir = obj.get("entry_dir").and_then(Value::as_str).unwrap_or("");
    let doc_id = obj.get("doc_id").and_then(Value::as_str).unwrap_or("");
    if entry_dir.is_empty() || doc_id.is_empty() {
        return Ok(None);
    }
    let bm25_chunk_dir = obj
        .get("bm25_chunk_dir")
        .and_then(Value::as_str)
        .map(PathBuf::from);
    let chunk_dir = bm25_chunk_dir.as_deref();
    let doc = load_merged_document_json(Path::new(entry_dir), doc_id, chunk_dir)?;
    Ok(doc.get("structure").cloned())
}

fn load_node_body(entry: &Value, node_id: u32) -> Result<String, String> {
    let Some(obj) = entry.as_object() else {
        return Ok(String::new());
    };
    let entry_dir = obj.get("entry_dir").and_then(Value::as_str).unwrap_or("");
    let doc_id = obj.get("doc_id").and_then(Value::as_str).unwrap_or("");
    if entry_dir.is_empty() || doc_id.is_empty() {
        return Ok(String::new());
    }
    let bm25_chunk_dir = obj
        .get("bm25_chunk_dir")
        .and_then(Value::as_str)
        .map(PathBuf::from);
    let chunk_dir = bm25_chunk_dir.as_deref();
    let index = load_skills_index_from_entry(Path::new(entry_dir), doc_id, chunk_dir)?;
    let spec = node_id.to_string();
    let rows = get_line_content(&index, doc_id, &[], &[spec.as_str()], &[]);
    let Some(arr) = rows.as_array() else {
        return Ok(String::new());
    };
    let content = arr
        .first()
        .and_then(|row| row.get("content"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    Ok(content)
}

/// Batch-load rerankable node bodies from cached skill entries.
///
/// # Errors
///
/// Returns an error when index loading fails.
pub fn build_skill_node_catalog(entries: &[Value]) -> Result<Vec<Value>, String> {
    let mut items = Vec::new();
    for entry in entries {
        let Some(obj) = entry.as_object() else {
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
        if entry_dir.is_empty() || doc_id.is_empty() {
            continue;
        }
        let file_path = obj
            .get("source_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let Some(structure) = load_structure_for_entry(entry)? else {
            continue;
        };
        for node_id in iter_content_node_ids(&structure) {
            let body = load_node_body(entry, node_id)?;
            if body.is_empty() {
                continue;
            }
            items.push(json!({
                "entry_dir": entry_dir,
                "doc_id": doc_id,
                "node_id": node_id,
                "file_path": file_path,
                "content": body,
                "score": "0.0",
            }));
        }
    }
    Ok(items)
}

fn apply_frontmatter_gate(
    entries: &[Value],
    query: &str,
    upper: f64,
) -> Result<(Vec<Value>, Value), String> {
    let (_excluded, trace) = bm25_frontmatter_gate(entries, query, upper)?;
    let excluded: HashSet<(String, String)> = trace
        .get("rows")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(|row| {
                    let obj = row.as_object()?;
                    if obj.get("passed").and_then(Value::as_bool).unwrap_or(true) {
                        return None;
                    }
                    Some((
                        obj.get("entry_dir")?.as_str()?.to_string(),
                        obj.get("doc_id")?.as_str()?.to_string(),
                    ))
                })
                .collect()
        })
        .unwrap_or_default();
    let eligible = entries
        .iter()
        .filter(|entry| {
            let Some(obj) = entry.as_object() else {
                return false;
            };
            let entry_dir = obj.get("entry_dir").and_then(Value::as_str).unwrap_or("");
            let doc_id = obj.get("doc_id").and_then(Value::as_str).unwrap_or("");
            !excluded.contains(&(entry_dir.to_string(), doc_id.to_string()))
        })
        .cloned()
        .collect();
    Ok((eligible, trace))
}

fn apply_budget_selection(
    lookup: &HashMap<(String, String), Value>,
    trace_rows: &[Value],
    matches: &[Value],
    options: &SearchSkillsOptions,
) -> Result<(Vec<Value>, Value), String> {
    let survivors: Vec<Value> = trace_rows
        .iter()
        .filter_map(|row| {
            let doc_id = row.get("doc_id").and_then(Value::as_str).unwrap_or("");
            let entry_dir = row.get("entry_dir").and_then(Value::as_str).unwrap_or("");
            let key = if entry_dir.is_empty() {
                lookup
                    .iter()
                    .find(|((_, d), _)| d == doc_id)
                    .map(|((e, _), v)| (e.clone(), v.clone()))
            } else {
                lookup
                    .get(&(entry_dir.to_string(), doc_id.to_string()))
                    .map(|v| (entry_dir.to_string(), v.clone()))
            };
            let (_, entry) = key?;
            Some(enrich_survivor(row, &entry))
        })
        .collect();
    if survivors.is_empty() {
        return Ok((matches.to_vec(), Value::Array(Vec::new())));
    }
    let selected = greedy_select_skill_items(&survivors, &options.item_kind, options.max_tokens)?;
    Ok((
        selected
            .get("matches")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        selected
            .get("budget_trace")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new())),
    ))
}

/// BM25 skill search, optional frontmatter gate, and greedy budget selection.
///
/// # Errors
///
/// Returns an error when BM25 scoring or selection fails.
pub fn search_skills_and_select(
    entries: &[Value],
    query: &str,
    options: &SearchSkillsOptions,
) -> Result<Value, String> {
    if query.trim().is_empty() || entries.is_empty() {
        return Ok(json!({
            "matches": [],
            "trace_rows": [],
            "threshold": options.threshold,
            "frontmatter_trace": { "rows": [], "upper_limit": options.frontmatter_upper_limit },
        }));
    }

    let lookup = entry_lookup(entries);
    let mut eligible = entries.to_vec();
    let mut frontmatter_trace = json!({ "rows": [], "upper_limit": Value::Null });

    if let Some(upper) = options.frontmatter_upper_limit {
        let (filtered, trace) = apply_frontmatter_gate(entries, query, upper)?;
        frontmatter_trace = trace;
        eligible = filtered;
    }

    let search_result =
        bm25_search_skill_chunks(&eligible, query, options.threshold, &HashSet::new())?;
    let mut matches = search_result
        .get("matches")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let trace_rows = search_result
        .get("trace_rows")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let threshold = search_result
        .get("threshold")
        .and_then(Value::as_f64)
        .unwrap_or(options.threshold);

    let budget_trace = if options.max_tokens > 0 && !trace_rows.is_empty() {
        let (selected_matches, trace) =
            apply_budget_selection(&lookup, &trace_rows, &matches, options)?;
        matches = selected_matches;
        trace
    } else {
        Value::Array(Vec::new())
    };

    Ok(json!({
        "matches": matches,
        "trace_rows": trace_rows,
        "threshold": threshold,
        "frontmatter_trace": frontmatter_trace,
        "budget_trace": budget_trace,
    }))
}
