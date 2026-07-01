use crate::paths::{self, decomposed_prefix, json_ext, md_ext};
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone)]
pub struct CatalogIndex {
    pub tools: Vec<Value>,
    pub files: HashMap<String, String>,
}

/// Parse a catalog index from a JSON value (`{ "tools": [...], "files": {...} }`).
#[must_use]
pub fn catalog_index_from_value(val: &Value) -> CatalogIndex {
    let tools = val
        .get("tools")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mut files = HashMap::new();
    if let Some(map) = val.get("files").and_then(|v| v.as_object()) {
        for (k, v) in map {
            if let Some(s) = v.as_str() {
                files.insert(k.clone(), s.to_string());
            }
        }
    }
    CatalogIndex { tools, files }
}

impl CatalogIndex {
    #[must_use]
    pub fn to_catalog_dict(&self) -> Value {
        self.to_catalog_dict_with_prefix(&crate::paths::catalog_prefix())
    }

    #[must_use]
    pub fn to_catalog_dict_with_prefix(&self, catalog_prefix: &str) -> Value {
        let mut md_entries = Vec::new();
        let mut json_entries = Vec::new();
        let mut paths: Vec<_> = self.files.keys().cloned().collect();
        paths.sort();

        for rel_path in paths {
            if !rel_path.starts_with(&decomposed_prefix()) {
                continue;
            }
            let content = &self.files[&rel_path];
            let file_path = format!("{catalog_prefix}/{rel_path}");
            if rel_path.ends_with(&md_ext()) {
                let id = path_stem(&rel_path);
                md_entries.push(json!({
                    "id": id,
                    "file_path": file_path,
                    "score": 1.0,
                    "start_line": 1,
                    "end_line": 1,
                    "language": "markdown",
                    "content": content,
                }));
            } else if rel_path.ends_with(&json_ext()) {
                let Ok(parsed) = serde_json::from_str::<Value>(content) else {
                    continue;
                };
                if !parsed.is_object() {
                    continue;
                }
                let line_count = content.lines().count();
                let entry_id = parsed.get("id").cloned().unwrap_or_else(|| {
                    Value::String(paths::tool_id_from_decomposed_rel(&rel_path))
                });
                json_entries.push(json!({
                    "id": entry_id,
                    "name": entry_id,
                    "file_path": file_path,
                    "score": 1.0,
                    "start_line": 1,
                    "end_line": line_count,
                    "language": "json",
                    "content": parsed,
                }));
            }
        }
        json!({
            "md": md_entries,
            "json": json_entries,
            "tools": self.tools,
        })
    }
}

fn path_stem(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

#[must_use]
pub fn catalog_tool_count(data: &Value) -> usize {
    if let Some(tools) = data.get("tools").and_then(|v| v.as_array())
        && !tools.is_empty()
    {
        return tools.len();
    }
    let Some(json_items) = data.get("json").and_then(|v| v.as_array()) else {
        return 0;
    };
    let mut tool_ids = HashSet::new();
    for item in json_items {
        let Some(obj) = item.as_object() else {
            continue;
        };
        if let Some(fp) = obj.get("file_path").and_then(|v| v.as_str())
            && !fp.is_empty()
        {
            tool_ids.insert(paths::tool_id_from_decomposed_rel(fp));
            continue;
        }
        let id = obj
            .get("id")
            .or_else(|| obj.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if !id.is_empty() {
            tool_ids.insert(id.to_string());
        }
    }
    tool_ids.len()
}

/// Plain-text form for decomposed enum `.md` files (matches Python ``str(val)``).
fn enum_markdown_value(val: &Value) -> String {
    match val {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        other => other.to_string(),
    }
}

#[must_use]
pub fn dedupe_enums(all_enums: &[Value]) -> Vec<Value> {
    let mut seen = HashSet::new();
    let mut unique = Vec::new();
    for val in all_enums {
        let key = serde_json::to_string(val).unwrap_or_default();
        if seen.insert(key) {
            unique.push(val.clone());
        }
    }
    unique.sort_by(|a, b| {
        serde_json::to_string(a)
            .unwrap_or_default()
            .cmp(&serde_json::to_string(b).unwrap_or_default())
    });
    unique
}

type PathSegment = Map<String, Value>;
type Extraction = (Vec<PathSegment>, Value);

fn segment(seg_type: &str, extra: Map<String, Value>) -> PathSegment {
    let mut m = Map::new();
    m.insert("type".into(), Value::String(seg_type.into()));
    for (k, v) in extra {
        m.insert(k, v);
    }
    m
}

fn build_property_file(tool_name: &str, path: &[PathSegment], leaf_schema: Value) -> Value {
    let mut current = leaf_schema;
    for segment in path.iter().rev() {
        let seg_type = segment.get("type").and_then(|v| v.as_str()).unwrap_or("");
        current = match seg_type {
            "properties" => {
                let name = segment.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let mut props = Map::new();
                props.insert(name.into(), current);
                json!({"properties": Value::Object(props)})
            }
            "items" => {
                if segment.contains_key("index") {
                    json!({"items": vec![current]})
                } else {
                    json!({"items": current})
                }
            }
            "allOf" | "anyOf" | "oneOf" => {
                let mut m = Map::new();
                m.insert(seg_type.into(), Value::Array(vec![current]));
                Value::Object(m)
            }
            "additionalProperties" => json!({"additionalProperties": current}),
            "patternProperties" => {
                let pat = segment
                    .get("pattern")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let mut pp = Map::new();
                pp.insert(pat.into(), current);
                json!({"patternProperties": Value::Object(pp)})
            }
            "if" | "then" | "else" | "not" | "contains" | "propertyNames" => {
                let mut m = Map::new();
                m.insert(seg_type.into(), current);
                Value::Object(m)
            }
            _ => current,
        };
    }
    json!({
        "id": tool_name,
        "name": tool_name,
        "inputSchema": current,
    })
}

fn process_node(
    node: &Value,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) -> Value {
    let Some(obj) = node.as_object() else {
        return node.clone();
    };
    let mut result: Map<String, Value> = obj.clone();
    process_compositions(&mut result, tool_name, server_name, path, extractions);

    if let Some(props) = result
        .get("properties")
        .and_then(|v| v.as_object())
        .cloned()
    {
        let req_props: HashSet<String> = result
            .get("required")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();

        let mut filtered = Map::new();
        for (prop_name, prop_schema) in props {
            let mut child_path = path.to_vec();
            let mut name_seg = Map::new();
            name_seg.insert("type".into(), Value::String("properties".into()));
            name_seg.insert("name".into(), Value::String(prop_name.clone()));
            child_path.push(name_seg);

            if req_props.contains(&prop_name) {
                filtered.insert(
                    prop_name.clone(),
                    process_node(
                        &prop_schema,
                        tool_name,
                        server_name,
                        &child_path,
                        extractions,
                    ),
                );
            } else {
                let filtered_child = process_node(
                    &prop_schema,
                    tool_name,
                    server_name,
                    &child_path,
                    extractions,
                );
                let prop_file = build_property_file(tool_name, &child_path, filtered_child);
                extractions.push((child_path, prop_file));
            }
        }
        result.insert("properties".into(), Value::Object(filtered));
    }
    Value::Object(result)
}

fn process_compositions(
    result: &mut Map<String, Value>,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) {
    handle_logical_compositions(result, tool_name, server_name, path, extractions);
    handle_conditional_compositions(result, tool_name, server_name, path, extractions);
    handle_array_properties(result, tool_name, server_name, path, extractions);
    handle_miscellaneous_keywords(result, tool_name, server_name, path, extractions);
}

fn handle_logical_compositions(
    result: &mut Map<String, Value>,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) {
    for key in ["allOf", "anyOf", "oneOf"] {
        if let Some(items) = result.get(key).and_then(|v| v.as_array()).cloned() {
            let processed: Vec<Value> = items
                .into_iter()
                .enumerate()
                .map(|(i, item)| {
                    let mut p = path.to_vec();
                    let mut seg = Map::new();
                    seg.insert("type".into(), Value::String(key.into()));
                    seg.insert("index".into(), Value::Number(i.into()));
                    p.push(seg);
                    process_node(&item, tool_name, server_name, &p, extractions)
                })
                .collect();
            result.insert(key.into(), Value::Array(processed));
        }
    }
}

fn handle_conditional_compositions(
    result: &mut Map<String, Value>,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) {
    for key in ["if", "then", "else"] {
        if result.contains_key(key) {
            let val = result.get(key).cloned().unwrap_or(Value::Null);
            let mut p = path.to_vec();
            p.push(segment(key, Map::new()));
            result.insert(
                key.into(),
                process_node(&val, tool_name, server_name, &p, extractions),
            );
        }
    }
    if result.contains_key("not") {
        let val = result.get("not").cloned().unwrap_or(Value::Null);
        let mut p = path.to_vec();
        p.push(segment("not", Map::new()));
        result.insert(
            "not".into(),
            process_node(&val, tool_name, server_name, &p, extractions),
        );
    }
}

fn handle_array_properties(
    result: &mut Map<String, Value>,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) {
    if let Some(items) = result.get("items").cloned() {
        let processed = if let Some(obj) = items.as_object() {
            let mut p = path.to_vec();
            p.push(segment("items", Map::new()));
            process_node(
                &Value::Object(obj.clone()),
                tool_name,
                server_name,
                &p,
                extractions,
            )
        } else if let Some(arr) = items.as_array() {
            Value::Array(
                arr.iter()
                    .enumerate()
                    .map(|(i, item)| {
                        let mut p = path.to_vec();
                        let mut seg = Map::new();
                        seg.insert("type".into(), Value::String("items".into()));
                        seg.insert("index".into(), Value::Number(i.into()));
                        p.push(seg);
                        process_node(item, tool_name, server_name, &p, extractions)
                    })
                    .collect(),
            )
        } else {
            items
        };
        result.insert("items".into(), processed);
    }
}

fn handle_miscellaneous_keywords(
    result: &mut Map<String, Value>,
    tool_name: &str,
    server_name: &str,
    path: &[PathSegment],
    extractions: &mut Vec<Extraction>,
) {
    for key in ["contains", "propertyNames", "additionalProperties"] {
        if let Some(obj) = result.get(key).and_then(|v| v.as_object()).cloned() {
            let mut p = path.to_vec();
            p.push(segment(key, Map::new()));
            result.insert(
                key.into(),
                process_node(&Value::Object(obj), tool_name, server_name, &p, extractions),
            );
        }
    }
    if let Some(pp) = result
        .get("patternProperties")
        .and_then(|v| v.as_object())
        .cloned()
    {
        let mut new_pp = Map::new();
        for (pat, sub) in pp {
            let mut p = path.to_vec();
            let mut seg = Map::new();
            seg.insert("type".into(), Value::String("patternProperties".into()));
            seg.insert("pattern".into(), Value::String(pat.clone()));
            p.push(seg);
            new_pp.insert(
                pat,
                process_node(&sub, tool_name, server_name, &p, extractions),
            );
        }
        result.insert("patternProperties".into(), Value::Object(new_pp));
    }
}

#[must_use]
pub fn decompose_tool_schema(tool_info: &Value) -> (Value, Vec<Extraction>) {
    let tool_id = tool_info.get("id").and_then(|v| v.as_str()).unwrap_or("");
    let t_desc = tool_info
        .get("full_schema")
        .and_then(|fs| fs.get("description"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let t_schema = tool_info
        .get("full_schema")
        .and_then(|fs| fs.get("inputSchema"))
        .cloned()
        .unwrap_or(Value::Null);
    let server = tool_info
        .get("server")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let mut extractions = Vec::new();
    let filtered = if t_schema.is_object() {
        process_node(&t_schema, tool_id, server, &[], &mut extractions)
    } else {
        t_schema
    };
    let root_schema = json!({
        "id": tool_id,
        "name": tool_id,
        "description": t_desc,
        "inputSchema": filtered,
    });
    (root_schema, extractions)
}

fn property_relative_path(tool_id: &str, path_segments: &[PathSegment], prop_name: &str) -> String {
    let prefix = decomposed_prefix().trim_end_matches('/').to_string();
    let mut parts = vec![prefix, tool_id.to_string()];
    for seg in path_segments
        .iter()
        .take(path_segments.len().saturating_sub(1))
    {
        let seg_type = seg.get("type").and_then(|v| v.as_str()).unwrap_or("");
        match seg_type {
            "properties" => {
                if let Some(name) = seg.get("name").and_then(|v| v.as_str()) {
                    parts.push(name.to_string());
                }
            }
            "patternProperties" => {
                if let Some(pat) = seg.get("pattern").and_then(|v| v.as_str()) {
                    parts.push(pat.to_string());
                }
            }
            _ => {}
        }
    }
    parts.push(format!("{prop_name}{}", json_ext()));
    parts.join("/")
}

#[must_use]
pub fn build_catalog_index(tools: &[Value], all_enums: &[Value]) -> CatalogIndex {
    let mut files = HashMap::new();

    for tool_info in tools {
        let tool_id = tool_info.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if let Some(full_schema) = tool_info.get("full_schema") {
            files.insert(
                format!("schemas/full/{tool_id}{}", json_ext()),
                serde_json::to_string_pretty(full_schema).unwrap_or_default(),
            );
        }
    }

    for val in dedupe_enums(all_enums) {
        let text = enum_markdown_value(&val);
        let key = format!("{}{text}{}", decomposed_prefix(), md_ext());
        files.insert(key, text);
    }

    for tool_info in tools {
        let tool_id = tool_info.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let (root_schema, extractions) = decompose_tool_schema(tool_info);
        files.insert(
            format!("{}{tool_id}{}", decomposed_prefix(), json_ext()),
            serde_json::to_string_pretty(&root_schema).unwrap_or_default(),
        );
        for (path_segments, prop_schema) in extractions {
            let prop_name = path_segments
                .last()
                .and_then(|s| s.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let rel_path = property_relative_path(tool_id, &path_segments, prop_name);
            files.insert(
                rel_path,
                serde_json::to_string_pretty(&prop_schema).unwrap_or_default(),
            );
        }
    }

    files.insert(
        "tools.json".into(),
        serde_json::to_string_pretty(tools).unwrap_or_default(),
    );

    CatalogIndex {
        tools: tools.to_vec(),
        files,
    }
}
