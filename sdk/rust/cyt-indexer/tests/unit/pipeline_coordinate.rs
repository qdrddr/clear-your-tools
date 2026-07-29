use cyt_indexer::build::catalog_index_from_value;
use cyt_indexer::pipeline::{CoordinateBm25Options, coordinate_bm25_prune};
use cyt_indexer::policies::policy_context_from_values;
use serde_json::json;

#[test]
fn coordinate_bm25_prune_empty_inputs() -> Result<(), String> {
    let index = catalog_index_from_value(&json!({"tools": [], "files": {}}));
    let ctx = policy_context_from_values(&json!({}));
    let result = coordinate_bm25_prune(
        &[],
        &json!({"json": [], "md": []}),
        &json!({"json": [], "md": []}),
        &index,
        "hello",
        &ctx,
        &ctx,
        &CoordinateBm25Options::default(),
    )?;
    assert!(result.get("skills").is_some());
    assert!(result.get("tools").is_some());
    Ok(())
}
