use cyt_indexer::{build_catalog_index, count_tokens};
use serde_json::json;

#[test]
fn count_tokens_from_registry_crate() {
    assert!(count_tokens("hello world") > 0);
}

#[test]
fn build_catalog_index_from_registry_crate() {
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
}
