//! Convert Anthropic API tools or catalog entries into the format expected by [`build_catalog_index`].
//! Port of `src/cyt/indexer/build.py` + `anthropic_tools_to_catalog_entries` in the proxy.

use crate::build::{CatalogIndex, build_catalog_index};
use crate::paths::collect_enums;
use serde_json::{Value, json};

use crate::tiktoken;

/// Truncate text to at most `max_tokens` (tiktoken), preferring a word boundary.
#[must_use]
pub fn truncate_description(description: &str, max_tokens: usize) -> String {
    tiktoken::truncate_description_or_passthrough(description, max_tokens)
}

fn anthropic_input_schema(tool: &Value) -> Value {
    tool.get("input_schema")
        .or_else(|| tool.get("inputSchema"))
        .or_else(|| tool.get("parameters"))
        .cloned()
        .unwrap_or_else(|| json!({}))
}

/// True when `tool` already matches catalog entry shape (`id` + `full_schema`).
pub fn is_catalog_tool_entry(tool: &Value) -> bool {
    tool.get("id")
        .and_then(Value::as_str)
        .is_some_and(|id| !id.is_empty())
        && tool.get("full_schema").is_some_and(Value::is_object)
}

/// Build one catalog entry from tool metadata (no file I/O).
#[must_use]
pub fn prepare_tool_entry(
    server_name: &str,
    name: &str,
    description: &str,
    input_schema: &Value,
) -> Value {
    let full_schema = json!({
        "id": name,
        "name": name,
        "description": description,
        "inputSchema": input_schema,
    });
    json!({
        "id": name,
        "server": server_name,
        "tool": name,
        "summary": truncate_description(description, 60),
        "full_schema": full_schema,
    })
}

/// Convert one Anthropic `{ name, description, input_schema }` tool to a catalog entry.
pub fn anthropic_tool_to_catalog_entry(tool: &Value) -> Option<Value> {
    let name = tool.get("name").and_then(Value::as_str)?;
    if name.is_empty() {
        return None;
    }
    let description = tool
        .get("description")
        .and_then(Value::as_str)
        .unwrap_or("");
    let input_schema = anthropic_input_schema(tool);
    Some(prepare_tool_entry("", name, description, &input_schema))
}

/// Normalize a tool list (Anthropic API and/or catalog entries) for indexing.
#[must_use]
pub fn normalize_tools_for_catalog(tools: &[Value]) -> (Vec<Value>, Vec<Value>) {
    let mut entries = Vec::with_capacity(tools.len());
    let mut all_enums = Vec::new();

    for tool in tools {
        let entry = if is_catalog_tool_entry(tool) {
            tool.clone()
        } else {
            match anthropic_tool_to_catalog_entry(tool) {
                Some(entry) => entry,
                None => continue,
            }
        };
        if let Some(schema) = entry.pointer("/full_schema/inputSchema") {
            all_enums.extend(collect_enums(schema));
        }
        entries.push(entry);
    }

    (entries, all_enums)
}

/// Build a decomposed catalog index from Anthropic API tools or pre-built catalog entries.
#[must_use]
pub fn build_catalog_from_tools(tools: &[Value]) -> CatalogIndex {
    let (entries, enums) = normalize_tools_for_catalog(tools);
    build_catalog_index(&entries, &enums)
}

/// Convert Anthropic API tools to catalog entries and collected enum values.
#[must_use]
pub fn anthropic_tools_to_catalog_entries(tools: &[Value]) -> (Vec<Value>, Vec<Value>) {
    let mut entries = Vec::new();
    let mut all_enums = Vec::new();
    for tool in tools {
        let Some(entry) = anthropic_tool_to_catalog_entry(tool) else {
            continue;
        };
        if let Some(schema) = entry.pointer("/full_schema/inputSchema") {
            all_enums.extend(collect_enums(schema));
        }
        entries.push(entry);
    }
    (entries, all_enums)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    #[test]
    fn anthropic_tool_produces_decomposed_files() {
        let tool = json!({
            "name": "Agent",
            "description": "Launch agents",
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "model": {"type": "string", "enum": ["opus", "haiku"]}
                },
                "required": ["prompt"]
            }
        });
        let index = build_catalog_from_tools(&[tool]);
        assert!(index.files.contains_key("schemas/decomposed/Agent.json"));
        assert!(index.files.keys().any(|k| k.contains("Agent/model")));
        assert!(index.files.contains_key("schemas/decomposed/haiku.md"));
        assert!(index.files.contains_key("schemas/full/metadata.json"));
        assert!(index.files.contains_key("schemas/decomposed/metadata.json"));
    }

    #[test]
    fn catalog_entry_passthrough() -> Result<(), String> {
        let tool = json!({
            "id": "mcp__test__foo",
            "server": "test",
            "tool": "mcp__test__foo",
            "summary": "A test tool",
            "full_schema": {
                "id": "mcp__test__foo",
                "name": "mcp__test__foo",
                "description": "A test tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "required_field": {"type": "string"},
                        "optional_field": {"type": "string"}
                    },
                    "required": ["required_field"]
                }
            }
        });
        let index = build_catalog_from_tools(&[tool]);
        assert!(
            index
                .files
                .contains_key("schemas/decomposed/mcp__test__foo.json")
        );
        assert!(index.files.keys().any(|k| k.contains("optional_field")));
        assert!(index.files.contains_key("schemas/full/metadata.json"));
        assert!(index.files.contains_key("schemas/decomposed/metadata.json"));
        let full_meta_raw = index
            .files
            .get("schemas/full/metadata.json")
            .ok_or_else(|| "missing schemas/full/metadata.json".to_string())?;
        let full_meta: Value = serde_json::from_str(full_meta_raw)
            .map_err(|e| format!("invalid schemas/full/metadata.json: {e}"))?;
        assert!(full_meta.get("token_count").is_some() || full_meta.get("files").is_some());
        let decomposed_meta_raw = index
            .files
            .get("schemas/decomposed/metadata.json")
            .ok_or_else(|| "missing schemas/decomposed/metadata.json".to_string())?;
        let decomposed_meta: Value = serde_json::from_str(decomposed_meta_raw)
            .map_err(|e| format!("invalid schemas/decomposed/metadata.json: {e}"))?;
        let entries = decomposed_meta
            .as_array()
            .ok_or_else(|| "decomposed metadata is not an array".to_string())?;
        assert!(!entries.is_empty());
        assert!(entries[0].get("file_path").is_some());
        assert!(entries[0].get("token_count").is_some());

        let catalog = index.to_catalog_dict();
        let json_items = catalog
            .get("json")
            .and_then(Value::as_array)
            .ok_or_else(|| "catalog json entries missing".to_string())?;
        assert!(
            json_items
                .iter()
                .any(|item| item.get("token_count").is_some())
        );
        Ok(())
    }

    #[test]
    fn truncate_short_text_unchanged() {
        let text = "short tool description";
        assert_eq!(truncate_description(text, 60), text);
    }
}
