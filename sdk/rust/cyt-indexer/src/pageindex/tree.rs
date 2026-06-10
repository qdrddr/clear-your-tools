use super::config::PageIndexConfig;
use super::node_id::node_id_value;
use super::parse::ContentNode;
use serde_json::{json, Map, Value};

/// Reserved node id for YAML frontmatter (`0.md`).
pub const NODE_ID_FRONTMATTER: u32 = 0;
/// Reserved node id for preamble body text (`1.md`).
pub const NODE_ID_PREAMBLE: u32 = 1;
/// First node id assigned to markdown heading sections.
pub const CONTENT_NODE_ID_START: u32 = 2;

/// Structure node kind for YAML frontmatter.
pub const NODE_KIND_FRONTMATTER: &str = "frontmatter";
/// Structure node kind for body text between YAML frontmatter and the first heading.
pub const NODE_KIND_PREAMBLE: &str = "preamble";

struct MutableNode {
    value: Value,
    level: usize,
    children: Vec<Self>,
}

/// Build a hierarchical JSON tree from flat markdown heading nodes.
#[must_use]
pub fn build_tree_from_nodes(node_list: &[ContentNode]) -> Value {
    if node_list.is_empty() {
        return Value::Array(vec![]);
    }

    let mut stack: Vec<MutableNode> = Vec::new();
    let mut roots: Vec<MutableNode> = Vec::new();

    for node in node_list {
        let current_level = node.level;
        let tree_node = MutableNode {
            value: json!({
                "title": node.title,
                "text": node.text,
                "line_num": node.line_num,
            }),
            level: current_level,
            children: Vec::new(),
        };

        while stack.last().is_some_and(|n| n.level >= current_level) {
            let Some(finished) = stack.pop() else {
                break;
            };
            if let Some(parent) = stack.last_mut() {
                parent.children.push(finished);
            } else {
                roots.push(finished);
            }
        }

        stack.push(tree_node);
    }

    while let Some(finished) = stack.pop() {
        if let Some(parent) = stack.last_mut() {
            parent.children.push(finished);
        } else {
            roots.push(finished);
        }
    }

    Value::Array(roots.into_iter().map(mutable_to_value).collect())
}

fn mutable_to_value(node: MutableNode) -> Value {
    let mut obj = node.value.as_object().cloned().unwrap_or_default();
    if !node.children.is_empty() {
        obj.insert(
            "nodes".to_string(),
            Value::Array(node.children.into_iter().map(mutable_to_value).collect()),
        );
    }
    Value::Object(obj)
}

pub fn write_node_id(structure: &mut Value, start: u32) -> u32 {
    match structure {
        Value::Object(map) => {
            let mut next = start;
            map.insert("node_id".to_string(), node_id_value(next));
            next += 1;
            if let Some(Value::Array(items)) = map.get_mut("nodes") {
                for item in items {
                    next = write_node_id(item, next);
                }
            }
            next
        }
        Value::Array(items) => {
            let mut next = start;
            for item in items {
                next = write_node_id(item, next);
            }
            next
        }
        _ => start,
    }
}

#[must_use]
pub fn structure_to_list(structure: &Value) -> Vec<Value> {
    let mut nodes = Vec::new();
    collect_nodes(structure, &mut nodes);
    nodes
}

fn collect_nodes(structure: &Value, out: &mut Vec<Value>) {
    match structure {
        Value::Object(map) => {
            out.push(Value::Object(map.clone()));
            if let Some(Value::Array(children)) = map.get("nodes") {
                for child in children {
                    collect_nodes(child, out);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_nodes(item, out);
            }
        }
        _ => {}
    }
}

#[must_use]
pub fn remove_fields(data: &Value, fields: &[&str]) -> Value {
    match data {
        Value::Object(map) => {
            let mut out = Map::new();
            for (k, v) in map {
                if fields.contains(&k.as_str()) {
                    continue;
                }
                out.insert(k.clone(), remove_fields(v, fields));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(|v| remove_fields(v, fields)).collect()),
        other => other.clone(),
    }
}

/// Returns true when the structure object is a decomposed frontmatter node (node id 0).
#[must_use]
pub fn is_frontmatter_node(obj: &Map<String, Value>) -> bool {
    obj.get("kind")
        .and_then(|v| v.as_str())
        .is_some_and(|kind| kind == NODE_KIND_FRONTMATTER)
}

/// Returns true when the structure object is a decomposed preamble node (node id 1).
#[must_use]
pub fn is_preamble_node(obj: &Map<String, Value>) -> bool {
    obj.get("kind")
        .and_then(|v| v.as_str())
        .is_some_and(|kind| kind == NODE_KIND_PREAMBLE)
}

fn prefix_node(kind: &str, node_id: u32, line_num: u32, text: &str) -> Value {
    json!({
        "node_id": node_id,
        "kind": kind,
        "line_num": line_num,
        "text": text,
    })
}

/// Assign node ids, optionally inject frontmatter (`0`) and preamble (`1`), and format for output.
///
/// Heading sections always start at [`CONTENT_NODE_ID_START`] even when frontmatter and/or
/// preamble are absent.
#[must_use]
pub fn finalize_skill_structure(
    mut tree: Value,
    frontmatter: Option<&str>,
    frontmatter_line_num: Option<u32>,
    preamble: Option<&str>,
    preamble_line_num: Option<u32>,
    config: &PageIndexConfig,
) -> Value {
    if config.if_add_node_id {
        write_node_id(&mut tree, CONTENT_NODE_ID_START);
    }

    let mut prefix_nodes: Vec<Value> = Vec::new();
    if let Some(text) = frontmatter.map(str::trim).filter(|text| !text.is_empty()) {
        let line_num = frontmatter_line_num.unwrap_or(1);
        prefix_nodes.push(prefix_node(
            NODE_KIND_FRONTMATTER,
            NODE_ID_FRONTMATTER,
            line_num,
            text,
        ));
    }
    if let Some(text) = preamble.map(str::trim).filter(|text| !text.is_empty()) {
        let line_num = preamble_line_num.unwrap_or(1);
        prefix_nodes.push(prefix_node(NODE_KIND_PREAMBLE, NODE_ID_PREAMBLE, line_num, text));
    }

    let merged = if prefix_nodes.is_empty() {
        tree
    } else {
        match tree {
            Value::Array(mut roots) => {
                roots.splice(0..0, prefix_nodes);
                Value::Array(roots)
            }
            Value::Object(_) => {
                let mut roots = prefix_nodes;
                roots.push(tree);
                Value::Array(roots)
            }
            other => other,
        }
    };

    let output_cfg = PageIndexConfig {
        if_add_node_id: config.if_add_node_id,
        if_add_node_text: true,
        bm25_cohesion: config.bm25_cohesion.clone(),
    };
    format_structure_for_output(&merged, &output_cfg)
}

#[must_use]
pub fn format_structure_for_output(structure: &Value, config: &PageIndexConfig) -> Value {
    let order: Vec<&str> = if config.if_add_node_text {
        vec!["title", "node_id", "kind", "line_num", "text", "chunks", "nodes"]
    } else {
        vec!["title", "node_id", "kind", "line_num", "chunks", "nodes"]
    };
    format_structure(structure, &order)
}

fn format_structure(structure: &Value, order: &[&str]) -> Value {
    match structure {
        Value::Object(map) => {
            let mut out = Map::new();
            for key in order {
                if let Some(val) = map.get(*key) {
                    if *key == "nodes" {
                        if let Value::Array(children) = val
                            && !children.is_empty()
                        {
                            out.insert(
                                key.to_string(),
                                Value::Array(
                                    children
                                        .iter()
                                        .map(|c| format_structure(c, order))
                                        .collect(),
                                ),
                            );
                        }
                    } else {
                        out.insert(key.to_string(), val.clone());
                    }
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| format_structure(v, order)).collect())
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pageindex::parse::{extract_node_text_content, extract_nodes_from_markdown};

    #[test]
    fn nested_headings_form_tree() {
        let md = "# Root\n\n## Child\n\nText\n\n# Second";
        let (headers, lines) = extract_nodes_from_markdown(md);
        let nodes = extract_node_text_content(&headers, &lines);
        let tree = build_tree_from_nodes(&nodes);
        let arr = tree.as_array();
        assert!(arr.is_some_and(|items| !items.is_empty()));
        let first = arr.and_then(|items| items.first()).and_then(|v| v.as_object());
        assert!(first.is_some_and(|obj| obj.contains_key("nodes")));
    }
}
