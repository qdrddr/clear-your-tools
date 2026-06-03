use cyt_indexer::build_catalog_from_tools;
use serde_json::json;

#[test]
fn build_simple_tool() {
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
                    "optional_field": {"type": "string", "description": "opt"}
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
fn build_from_anthropic_tools() {
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
}

#[test]
fn enum_md_files_without_json_quotes() {
    let index = cyt_indexer::build_catalog_index(&[], &[json!("Bash"), json!("auto")]);
    assert_eq!(
        index.files.get("schemas/decomposed/Bash.md").map(String::as_str),
        Some("Bash"),
    );
    assert_eq!(
        index.files.get("schemas/decomposed/auto.md").map(String::as_str),
        Some("auto"),
    );
}
