//! Precise branch assertions for `resolve_build_catalog` (mutation killers).

use cyt_indexer::{build_catalog_from_tools, resolve_build_catalog};
use serde_json::json;

#[test]
fn index_shape_returns_enriched_catalog_dict() {
    let tool = json!({
        "name": "Agent",
        "description": "Launch agents",
        "input_schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"]
        }
    });
    let index = build_catalog_from_tools(&[tool]);
    let files_obj: serde_json::Map<String, serde_json::Value> = index
        .files
        .iter()
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect();
    let catalog = json!({"tools": index.tools, "files": files_obj});
    let fallback = json!({"json": [{"file_path": "should-not-win"}], "md": []});

    let resolved = resolve_build_catalog(&catalog, &fallback);
    assert_ne!(resolved, fallback);
    assert!(
        resolved
            .get("json")
            .and_then(serde_json::Value::as_array)
            .is_some_and(|arr| !arr.is_empty())
    );
    assert!(
        resolved["json"]
            .as_array()
            .and_then(|arr| arr.first())
            .and_then(|entry| entry.get("file_path"))
            .and_then(serde_json::Value::as_str)
            .is_some_and(|fp| fp.contains("Agent.json"))
    );
}

#[test]
fn non_empty_json_catalog_is_returned_unchanged() {
    let catalog = json!({
        "json": [{"file_path": "schemas/decomposed/foo.json", "content": {}}],
        "md": []
    });
    let fallback = json!({"json": [], "md": [{"file_path": "schemas/decomposed/bar.md"}]});
    let resolved = resolve_build_catalog(&catalog, &fallback);
    assert_eq!(resolved, catalog);
}

#[test]
fn empty_catalog_falls_back_to_survivor_data() {
    let catalog = json!({});
    let survivor = json!({
        "json": [{"file_path": "schemas/decomposed/keep.json", "score": "0.5"}],
        "md": []
    });
    let resolved = resolve_build_catalog(&catalog, &survivor);
    assert_eq!(resolved, survivor);
}

#[test]
fn empty_json_array_does_not_short_circuit_to_catalog() {
    let catalog = json!({"json": [], "md": []});
    let survivor = json!({
        "json": [{"file_path": "schemas/decomposed/survivor.json"}],
        "md": []
    });
    let resolved = resolve_build_catalog(&catalog, &survivor);
    assert_eq!(resolved, survivor);
}
