use std::collections::{HashMap, HashSet};

use serde_json::{json, Value};

use super::chunk_id::parse_chunk_ids;
use super::node_id::{node_id_from_value, parse_node_id_token};
use super::tree::{remove_fields, structure_to_list};
use super::types::{chunk_md_rel, node_md_rel, SkillDocument, SkillsIndex};

fn u64_to_u32(value: u64) -> u32 {
    u32::try_from(value).unwrap_or(0)
}

/// Parse a line-number spec such as `"5-7"`, `"3,8"`, or `"12"`.
///
/// # Errors
///
/// Returns an error when the format is invalid or a range is reversed.
pub fn parse_line_nums(spec: &str) -> Result<Vec<u32>, String> {
    let mut result = Vec::new();
    for part in spec.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some((start, end)) = part.split_once('-') {
            let start: u32 = start
                .trim()
                .parse()
                .map_err(|_| format!("invalid line_num range start in '{part}'"))?;
            let end: u32 = end
                .trim()
                .parse()
                .map_err(|_| format!("invalid line_num range end in '{part}'"))?;
            if start > end {
                return Err(format!("invalid range '{part}': start must be <= end"));
            }
            result.extend(start..=end);
        } else {
            let n: u32 = part
                .parse()
                .map_err(|_| format!("invalid line_num '{part}'"))?;
            result.push(n);
        }
    }
    result.sort_unstable();
    result.dedup();
    Ok(result)
}

/// Parse a node-id spec such as `"5-7"`, `"3,8"`, or `"12"`.
///
/// # Errors
///
/// Returns an error when the format is invalid or a range is reversed.
pub fn parse_node_ids(spec: &str) -> Result<Vec<u32>, String> {
    let mut result = Vec::new();
    for part in spec.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some((start, end)) = part.split_once('-') {
            let start = parse_node_id_token(start.trim())?;
            let end = parse_node_id_token(end.trim())?;
            if start > end {
                return Err(format!("invalid node_id range '{part}': start must be <= end"));
            }
            result.extend(start..=end);
        } else {
            result.push(parse_node_id_token(part)?);
        }
    }
    result.sort_unstable();
    result.dedup();
    Ok(result)
}

pub(crate) fn merge_line_num_specs(specs: &[&str]) -> Result<Vec<u32>, String> {
    let mut merged = Vec::new();
    for spec in specs {
        merged.extend(parse_line_nums(spec)?);
    }
    merged.sort_unstable();
    merged.dedup();
    Ok(merged)
}

pub(crate) fn merge_node_id_specs(specs: &[&str]) -> Result<Vec<u32>, String> {
    let mut merged = Vec::new();
    for spec in specs {
        merged.extend(parse_node_ids(spec)?);
    }
    merged.sort_unstable();
    merged.dedup();
    Ok(merged)
}

/// Return document metadata for a skill document.
#[must_use]
pub fn get_document<S: std::hash::BuildHasher>(
    documents: &HashMap<String, SkillDocument, S>,
    doc_id: &str,
) -> Value {
    let Some(doc) = documents.get(doc_id) else {
        return json!({ "error": format!("Document {doc_id} not found") });
    };
    json!({
        "doc_id": doc_id,
        "doc_name": doc.doc_name,
        "type": doc.doc_type,
        "status": "completed",
        "line_count": doc.line_count,
    })
}

/// Return the document tree with `text` fields removed.
#[must_use]
pub fn get_document_structure<S: std::hash::BuildHasher>(
    documents: &HashMap<String, SkillDocument, S>,
    doc_id: &str,
) -> Value {
    let Some(doc) = documents.get(doc_id) else {
        return json!({ "error": format!("Document {doc_id} not found") });
    };
    remove_fields(&doc.structure, &["text"])
}

pub(crate) fn merge_chunk_id_specs(specs: &[&str]) -> Result<Vec<u32>, String> {
    let mut merged = Vec::new();
    for spec in specs {
        merged.extend(parse_chunk_ids(spec)?);
    }
    merged.sort_unstable();
    merged.dedup();
    Ok(merged)
}

/// Return line content for nodes matched by line numbers, node ids, and/or chunk ids.
#[must_use]
pub fn get_line_content(
    index: &SkillsIndex,
    doc_id: &str,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
    chunk_id_specs: &[&str],
) -> Value {
    let Some(doc) = index.documents.get(doc_id) else {
        return json!({ "error": format!("Document {doc_id} not found") });
    };

    if line_num_specs.is_empty() && node_id_specs.is_empty() && chunk_id_specs.is_empty() {
        return json!({ "error": "content query requires at least one --line_num, --node_id, or --chunk_id" });
    }

    let line_nums = match merge_line_num_specs(line_num_specs) {
        Ok(nums) => nums,
        Err(e) => {
            return json!({ "error": format!("Invalid line_num format. Use \"5-7\", \"3,8\", or \"12\". Error: {e}") });
        }
    };
    let node_ids = match merge_node_id_specs(node_id_specs) {
        Ok(ids) => ids,
        Err(e) => {
            return json!({ "error": format!("Invalid node_id format. Use \"5-7\", \"3,8\", or \"12\". Error: {e}") });
        }
    };
    let chunk_ids = match merge_chunk_id_specs(chunk_id_specs) {
        Ok(ids) => ids,
        Err(e) => {
            return json!({ "error": format!("Invalid chunk_id format. Error: {e}") });
        }
    };

    if line_nums.is_empty() && node_ids.is_empty() && chunk_ids.is_empty() {
        return json!([]);
    }

    let mut results = Vec::new();

    if !chunk_ids.is_empty() {
        let chunk_set: HashSet<u32> = chunk_ids.into_iter().collect();
        for chunk_id in chunk_set {
            let rel = chunk_md_rel(doc_id, chunk_id);
            if let Some(raw) = index.files.get(&rel) {
                results.push(json!({
                    "chunk_id": chunk_id,
                    "content": strip_decomposed_frontmatter(raw),
                }));
            }
        }
        results.sort_by_key(|v| v.get("chunk_id").and_then(Value::as_u64).unwrap_or(0));
        return Value::Array(results);
    }

    let line_set: HashSet<u32> = line_nums.into_iter().collect();
    let node_set: HashSet<u32> = node_ids.into_iter().collect();
    let match_by_line = !line_set.is_empty();
    let match_by_node = !node_set.is_empty();

    let mut seen = HashSet::new();

    let flat_nodes = structure_to_list(&doc.structure);
    for node in flat_nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let line_num = obj
            .get("line_num")
            .and_then(serde_json::Value::as_u64)
            .map_or(0, u64_to_u32);
        let node_id = node_id_from_value(obj.get("node_id"));

        let matched = (match_by_line && line_set.contains(&line_num))
            || (match_by_node && node_set.contains(&node_id));
        if !matched || !seen.insert(node_id) {
            continue;
        }

        let content = resolve_node_content(index, doc_id, obj);
        results.push(json!({
            "line_num": line_num,
            "node_id": node_id,
            "content": content,
        }));
    }

    results.sort_by_key(|v| {
        v.get("line_num")
            .and_then(serde_json::Value::as_u64)
            .map_or(0, u64_to_u32)
    });
    Value::Array(results)
}

/// Convenience wrapper for a single `line_num` spec string.
#[must_use]
pub fn get_line_content_from_spec(index: &SkillsIndex, doc_id: &str, line_num_spec: &str) -> Value {
    get_line_content(index, doc_id, &[line_num_spec], &[], &[])
}

fn resolve_node_content(index: &SkillsIndex, doc_id: &str, node: &serde_json::Map<String, Value>) -> String {
    if let Some(Value::String(text)) = node.get("text")
        && !text.is_empty()
    {
        return text.clone();
    }

    let node_id = node_id_from_value(node.get("node_id"));
    let rel = node_md_rel(doc_id, node_id);
    if let Some(raw) = index.files.get(&rel) {
        return strip_decomposed_frontmatter(raw);
    }
    String::new()
}

pub(crate) fn strip_decomposed_frontmatter(content: &str) -> String {
    if !content.starts_with("---") {
        return content.to_string();
    }
    if let Some(end) = content[3..].find("\n---") {
        let body_start = 3 + end + 4;
        return content.get(body_start..).unwrap_or("").trim_start().to_string();
    }
    content.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_line_num_variants() {
        assert_eq!(parse_line_nums("5-7"), Ok(vec![5, 6, 7]));
        assert_eq!(parse_line_nums("3,8"), Ok(vec![3, 8]));
        assert_eq!(parse_line_nums("12"), Ok(vec![12]));
    }

    #[test]
    fn parse_node_id_variants() {
        assert_eq!(parse_node_ids("5-7"), Ok(vec![5, 6, 7]));
        assert_eq!(parse_node_ids("3,8"), Ok(vec![3, 8]));
        assert_eq!(parse_node_ids("0012"), Ok(vec![12]));
    }
}
