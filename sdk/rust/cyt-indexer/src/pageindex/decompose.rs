use serde_json::Value;

use super::tree::structure_to_list;
use super::types::{document_json_rel, node_md_rel, SkillDocument, SkillsIndex};

pub fn decompose_document(index: &mut SkillsIndex, doc: &SkillDocument, flat_structure: &Value) {
    let doc_json = serde_json::to_string_pretty(&doc.to_json()).unwrap_or_default();
    index
        .files
        .insert(document_json_rel(&doc.id), doc_json);

    let nodes = structure_to_list(flat_structure);
    for node in nodes {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let node_id = obj
            .get("node_id")
            .and_then(|v| v.as_str())
            .unwrap_or("0000");
        let title = obj.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let line_num = obj.get("line_num").and_then(serde_json::Value::as_u64).unwrap_or(0);
        let text = obj.get("text").and_then(|v| v.as_str()).unwrap_or("");

        let body = if text.is_empty() {
            format!("# {title}\n")
        } else {
            format!("# {title}\n\n{text}")
        };

        let md_content = format!(
            "---\ndoc_id: {}\nnode_id: \"{node_id}\"\nline_num: {line_num}\ntitle: {title}\n---\n{body}",
            doc.id
        );

        index
            .files
            .insert(node_md_rel(&doc.id, node_id), md_content);
    }
}
