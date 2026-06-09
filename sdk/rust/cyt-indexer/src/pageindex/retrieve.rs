use std::collections::HashMap;

use serde_json::{json, Value};

use super::tree::{remove_fields, structure_to_list};
use super::types::{node_md_rel, SkillDocument, SkillsIndex};

fn u64_to_u32(value: u64) -> u32 {
    u32::try_from(value).unwrap_or(0)
}

/// Parse a pages spec such as `"5-7"`, `"3,8"`, or `"12"`.
///
/// # Errors
///
/// Returns an error when the format is invalid or a range is reversed.
pub fn parse_pages(pages: &str) -> Result<Vec<u32>, String> {
    let mut result = Vec::new();
    for part in pages.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some((start, end)) = part.split_once('-') {
            let start: u32 = start
                .trim()
                .parse()
                .map_err(|_| format!("invalid page range start in '{part}'"))?;
            let end: u32 = end
                .trim()
                .parse()
                .map_err(|_| format!("invalid page range end in '{part}'"))?;
            if start > end {
                return Err(format!("invalid range '{part}': start must be <= end"));
            }
            result.extend(start..=end);
        } else {
            let n: u32 = part
                .parse()
                .map_err(|_| format!("invalid page number '{part}'"))?;
            result.push(n);
        }
    }
    result.sort_unstable();
    result.dedup();
    Ok(result)
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

/// Return page content for line numbers in `pages`.
#[must_use]
pub fn get_page_content(index: &SkillsIndex, doc_id: &str, pages: &str) -> Value {
    let Some(doc) = index.documents.get(doc_id) else {
        return json!({ "error": format!("Document {doc_id} not found") });
    };

    let page_nums = match parse_pages(pages) {
        Ok(nums) => nums,
        Err(e) => {
            return json!({ "error": format!("Invalid pages format: {pages:?}. Use \"5-7\", \"3,8\", or \"12\". Error: {e}") });
        }
    };

    if page_nums.is_empty() {
        return json!([]);
    }

    let min_line = page_nums[0];
    let max_line = page_nums[page_nums.len() - 1];
    let mut results = Vec::new();
    let mut seen = std::collections::HashSet::new();

    let flat_nodes = structure_to_list(&doc.structure);
    for node in flat_nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let line_num = obj
            .get("line_num")
            .and_then(serde_json::Value::as_u64)
            .map_or(0, u64_to_u32);
        if line_num < min_line || line_num > max_line || !seen.insert(line_num) {
            continue;
        }

        let content = resolve_node_content(index, doc_id, obj);
        results.push(json!({ "page": line_num, "content": content }));
    }

    results.sort_by_key(|v| {
        v.get("page")
            .and_then(serde_json::Value::as_u64)
            .map_or(0, u64_to_u32)
    });
    Value::Array(results)
}

fn resolve_node_content(index: &SkillsIndex, doc_id: &str, node: &serde_json::Map<String, Value>) -> String {
    if let Some(Value::String(text)) = node.get("text")
        && !text.is_empty()
    {
        return text.clone();
    }

    let node_id = node.get("node_id").and_then(|v| v.as_str()).unwrap_or("0000");
    let rel = node_md_rel(doc_id, node_id);
    if let Some(raw) = index.files.get(&rel) {
        return strip_frontmatter(raw);
    }
    String::new()
}

fn strip_frontmatter(content: &str) -> String {
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
    fn parse_pages_variants() {
        assert_eq!(parse_pages("5-7"), Ok(vec![5, 6, 7]));
        assert_eq!(parse_pages("3,8"), Ok(vec![3, 8]));
        assert_eq!(parse_pages("12"), Ok(vec![12]));
    }
}
