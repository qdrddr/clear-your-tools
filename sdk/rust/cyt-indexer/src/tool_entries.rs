//! Convert Anthropic API tools into catalog entries — tiktoken-backed description truncation.

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
