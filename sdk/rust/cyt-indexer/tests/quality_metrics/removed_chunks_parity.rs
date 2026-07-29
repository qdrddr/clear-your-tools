//! Python SDK parity for retrieve helpers (`test_removed_chunks.py` mirror).

use cyt_indexer::{RemovedChunksOptions, chunk_survivor_key, removed_chunks};
use serde_json::json;

#[test]
fn removed_chunks_excludes_survivors_by_decomposed_key() {
    let full = json!({
        "json": [
            {"file_path": "schemas/decomposed/Agent.json", "content": {"name": "Agent"}},
            {"file_path": "schemas/decomposed/Agent/extra.json", "content": {}},
        ],
        "md": [
            {"file_path": "schemas/decomposed/haiku.md", "content": "haiku"},
            {"file_path": "schemas/decomposed/sonnet.md", "content": "sonnet"},
        ],
    });
    let surviving = json!({
        "json": [{"file_path": "src/catalog/schemas/decomposed/Agent.json"}],
        "md": [{"file_path": "src/catalog/schemas/decomposed/haiku.md"}],
    });
    let removed = removed_chunks(&full, &surviving, &RemovedChunksOptions::default());
    let json_removed = removed.get("json").and_then(serde_json::Value::as_array);
    assert_eq!(json_removed.map(std::vec::Vec::len), Some(1));
    assert_eq!(
        json_removed
            .and_then(|entries| entries.first())
            .and_then(|entry| entry.get("file_path"))
            .and_then(serde_json::Value::as_str),
        Some("schemas/decomposed/Agent/extra.json")
    );
    let md_removed = removed.get("md").and_then(serde_json::Value::as_array);
    assert_eq!(md_removed.map(std::vec::Vec::len), Some(1));
    assert_eq!(
        md_removed
            .and_then(|entries| entries.first())
            .and_then(|entry| entry.get("file_path"))
            .and_then(serde_json::Value::as_str),
        Some("schemas/decomposed/sonnet.md")
    );
}

#[test]
fn chunk_survivor_key_normalizes_paths() {
    assert_eq!(
        chunk_survivor_key(
            &json!({"file_path": "src/catalog/schemas/decomposed/Agent.json"}),
            "json",
        ),
        Some("schemas/decomposed/Agent.json".to_string())
    );
}
