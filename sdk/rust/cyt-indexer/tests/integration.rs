use cyt_indexer::{build_catalog_index, count_tokens};
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
    let index = build_catalog_index(&[tool], &[]);
    assert!(index
        .files
        .contains_key("schemas/decomposed/mcp__test__foo.json"));
    assert!(index.files.keys().any(|k| k.contains("optional_field")));
}

#[test]
fn enum_md_files_without_json_quotes() {
    let index = build_catalog_index(&[], &[json!("Bash"), json!("auto")]);
    assert_eq!(
        index.files.get("schemas/decomposed/Bash.md").map(String::as_str),
        Some("Bash"),
    );
    assert_eq!(
        index.files.get("schemas/decomposed/auto.md").map(String::as_str),
        Some("auto"),
    );
}

#[test]
fn count_tokens_basic() {
    assert!(count_tokens("hello world") > 0);
}
