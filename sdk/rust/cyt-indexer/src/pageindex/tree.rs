use super::config::PageIndexConfig;
use super::parse::ContentNode;
use serde_json::{json, Map, Value};

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

    for (node_counter, node) in (1usize..).zip(node_list.iter()) {
        let current_level = node.level;
        let tree_node = MutableNode {
            value: json!({
                "title": node.title,
                "node_id": format!("{node_counter:04}"),
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

pub fn write_node_id(structure: &mut Value, start: usize) -> usize {
    match structure {
        Value::Object(map) => {
            let mut next = start;
            map.insert("node_id".to_string(), Value::String(format!("{next:04}")));
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

#[must_use]
pub fn format_structure_for_output(structure: &Value, config: &PageIndexConfig) -> Value {
    let order: Vec<&str> = if config.if_add_node_text {
        vec!["title", "node_id", "line_num", "text", "nodes"]
    } else {
        vec!["title", "node_id", "line_num", "nodes"]
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
