use crate::json_util::value_to_string;
use serde_json::Value;
use std::fmt::Write;

fn extract_level_info_value(data: &Value, results: &mut Vec<String>) {
    match data {
        Value::Object(map) => {
            let desc = map.get("description").and_then(|v| v.as_str());
            let default_val = map.get("default");
            let enums = map.get("enum").and_then(|v| v.as_array());

            if let Some(desc) = desc {
                let mut line = desc.to_string();
                if let Some(d) = default_val
                    && !d.is_null()
                {
                    let _ = write!(line, "; Default: {}", value_to_string(d));
                }
                if let Some(items) = enums
                    && !items.is_empty()
                {
                    let enums_str: Vec<String> = items.iter().map(value_to_string).collect();
                    let _ = write!(line, "; Options: {}", enums_str.join(", "));
                }
                results.push(line);
            }

            for val in map.values() {
                extract_level_info_value(val, results);
            }
        }
        Value::Array(items) => {
            for item in items {
                extract_level_info_value(item, results);
            }
        }
        _ => {}
    }
}

#[must_use]
pub fn extract_level_info(data: &Value) -> Vec<String> {
    let mut results = Vec::new();
    extract_level_info_value(data, &mut results);
    results
}

#[must_use]
pub fn extract_document_text(item_content: &Value) -> Option<String> {
    let level_lines = extract_level_info(item_content);
    if level_lines.is_empty() {
        return None;
    }
    Some(level_lines.join("\n"))
}

#[must_use]
pub fn extract_json_catalog_document(item: &Value) -> Option<String> {
    let obj = item.as_object()?;
    let content = obj.get("content")?;
    extract_document_text(content)
}

#[must_use]
pub fn extract_md_catalog_document(item: &Value) -> Option<String> {
    let obj = item.as_object()?;
    let content = obj.get("content")?;
    if content.is_null() {
        return None;
    }
    Some(value_to_string(content))
}
