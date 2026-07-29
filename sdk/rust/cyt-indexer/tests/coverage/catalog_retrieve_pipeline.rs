//! Broad retrieve + BM25 paths for llvm-cov (build → score → diff survivors).

use cyt_indexer::{
    RemovedChunksOptions, ScoreCatalogOptions, build_catalog_from_tools, removed_chunks,
    resolve_build_catalog, score_catalog_in_place,
};
use serde_json::json;

#[test]
fn build_score_and_diff_removed_chunks() -> Result<(), String> {
    let tool = json!({
        "name": "read_file",
        "description": "Read files from disk path and return their contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filesystem path"}
            },
            "required": ["path"]
        }
    });
    let index = build_catalog_from_tools(&[tool]);
    let files_obj: serde_json::Map<String, serde_json::Value> = index
        .files
        .iter()
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect();
    let build_catalog = json!({
        "tools": index.tools,
        "files": files_obj,
    });

    let mut survivor = resolve_build_catalog(&build_catalog, &json!({"json": [], "md": []}));
    score_catalog_in_place(
        &mut survivor,
        "read files from disk",
        &ScoreCatalogOptions::default(),
    )?;

    let json_entries = survivor
        .get("json")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| "expected json survivor entries".to_string())?;
    assert!(!json_entries.is_empty());

    let full = resolve_build_catalog(&build_catalog, &survivor);
    let pruned_survivor = json!({
        "json": [json_entries[0].clone()],
        "md": survivor.get("md").cloned().unwrap_or_else(|| json!([])),
    });
    let removed = removed_chunks(&full, &pruned_survivor, &RemovedChunksOptions::default());
    let removed_json = removed
        .get("json")
        .and_then(serde_json::Value::as_array)
        .map_or(0, std::vec::Vec::len);
    assert!(
        removed_json > 0 || json_entries.len() == 1,
        "multi-entry catalog should report pruned json chunks"
    );
    Ok(())
}

#[test]
fn resolve_build_catalog_from_index_enriches_token_fields() -> Result<(), String> {
    let tool = json!({
        "name": "write_file",
        "description": "Write bytes to a path on disk.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    });
    let index = build_catalog_from_tools(&[tool]);
    let files_obj: serde_json::Map<String, serde_json::Value> = index
        .files
        .iter()
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect();
    let catalog = json!({"tools": index.tools, "files": files_obj});
    let resolved = resolve_build_catalog(&catalog, &json!({}));
    let first = resolved
        .get("json")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .ok_or_else(|| "missing resolved json entry".to_string())?;
    assert!(
        first.get("token_count").is_some(),
        "cyt-indexer catalog dict should attach token_count placeholders"
    );
    Ok(())
}
