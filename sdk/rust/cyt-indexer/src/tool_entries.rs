//! Convert Anthropic API tools or catalog entries into the format expected by [`build_catalog_index`].
//! Port of `src/cyt/indexer/build.py` + `anthropic_tools_to_catalog_entries` in the proxy.

use crate::build::{build_catalog_index, CatalogIndex};
use crate::paths::collect_enums;
use serde_json::{json, Value};

/// Rough `cl100k_base` token estimate for summary truncation (conservative vs tiktoken).
fn approximate_token_count(text: &str) -> usize {
    text.chars().map(|c| if c.is_ascii() { 1u32 } else { 2 }).sum::<u32>() as usize / 2 + 1
}

/// Truncate text to at most `max_tokens` (approximate), preferring a word boundary.
#[must_use]
pub fn truncate_description(description: &str, max_tokens: usize) -> String {
    if description.is_empty() {
        return String::new();
    }
    if approximate_token_count(description) <= max_tokens {
        return description.to_string();
    }

    let suffix = "...";
    let suffix_tokens = approximate_token_count(suffix);
    let body_budget = max_tokens.saturating_sub(suffix_tokens);
    if body_budget == 0 {
        return suffix.to_string();
    }

    let chars: Vec<char> = description.chars().collect();
    let mut lo = 0usize;
    let mut hi = chars.len();
    while lo < hi {
        let mid = (lo + hi).div_ceil(2);
        let slice: String = chars[..mid].iter().collect();
        if approximate_token_count(&slice) <= body_budget {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }

    let mut body: String = chars[..lo].iter().collect();
    if let Some(sp) = body.rfind(' ')
        && sp > 0 {
            body.truncate(sp);
        }

    format!("{body}{suffix}")
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
    use serde_json::json;

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
        assert!(index
            .files
            .keys()
            .any(|k| k.contains("Agent/model")));
        assert!(index.files.contains_key("schemas/decomposed/haiku.md"));
    }

    #[test]
    fn catalog_entry_passthrough() {
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
        assert!(index
            .files
            .contains_key("schemas/decomposed/mcp__test__foo.json"));
        assert!(index.files.keys().any(|k| k.contains("optional_field")));
    }

    #[test]
    fn truncate_short_text_unchanged() {
        let text = "short tool description";
        assert_eq!(truncate_description(text, 60), text);
    }
}
