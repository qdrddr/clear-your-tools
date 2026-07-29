use cyt_indexer::cache::{original_definition_for_entry, tool_definition_content_hash};
use serde_json::json;

#[test]
fn tool_definition_hash_ignores_policy_and_is_stable() {
    let def = json!({
        "id": "mcp__test__foo",
        "name": "mcp__test__foo",
        "description": "A test tool",
        "inputSchema": {"type": "object", "properties": {}}
    });
    let h1 = tool_definition_content_hash(&def);
    let h2 = tool_definition_content_hash(&def);
    assert_eq!(h1, h2);
    assert_eq!(h1.len(), 64);
    assert_ne!(h1, tool_definition_content_hash(&json!({"id": "other"})));
}

#[test]
fn catalog_entry_hashes_full_schema_not_wrapper() {
    let entry = json!({
        "id": "mcp__test__foo",
        "server": "test",
        "tool": "mcp__test__foo",
        "summary": "short",
        "full_schema": {
            "id": "mcp__test__foo",
            "name": "mcp__test__foo",
            "description": "A test tool",
            "inputSchema": {"type": "object", "properties": {}}
        }
    });
    let from_entry = tool_definition_content_hash(&entry["full_schema"]);
    let from_wrapper = tool_definition_content_hash(&entry);
    assert_ne!(from_entry, from_wrapper);
    let original = original_definition_for_entry(&entry);
    assert!(original.is_some());
    if let Some(definition) = original {
        assert_eq!(from_entry, tool_definition_content_hash(&definition));
    }
}
