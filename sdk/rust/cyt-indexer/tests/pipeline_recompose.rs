#![allow(clippy::expect_used, clippy::unwrap_used)]

use cyt_indexer::build_catalog_from_tools;
use cyt_indexer::pipeline::recompose_and_retrieve_tools;
use cyt_indexer::policies::{PolicyContext, policy_context_from_values};
use serde_json::json;

fn default_ctx() -> PolicyContext {
    policy_context_from_values(&json!({}))
}

#[test]
fn recompose_injects_root_when_optional_json_survives() {
    let tool = json!({
        "id": "mcp__test__root_inject",
        "server": "test",
        "tool": "mcp__test__root_inject",
        "summary": "Root inject test",
        "full_schema": {
            "name": "mcp__test__root_inject",
            "description": "Root inject test",
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
    let catalog = index.to_catalog_dict();

    let optional = catalog
        .get("json")
        .and_then(|v| v.as_array())
        .and_then(|items| {
            items.iter().find(|item| {
                item.get("file_path")
                    .and_then(|v| v.as_str())
                    .is_some_and(|p| p.contains("optional_field"))
            })
        })
        .cloned()
        .expect("optional chunk");
    let mut optional = optional;
    if let Some(obj) = optional.as_object_mut() {
        obj.insert("score".into(), json!(0.9));
    }

    let data = json!({"json": [optional.clone()], "md": []});
    let post_scored = json!({"json": catalog.get("json").cloned().unwrap_or_default(), "md": []});
    let ctx = default_ctx();
    let pipeline = vec!["bm25".to_string()];

    let tools = recompose_and_retrieve_tools(
        &data,
        &catalog,
        &index,
        None,
        Some(&post_scored),
        None,
        &pipeline,
        &ctx,
        &ctx,
    );
    assert_eq!(tools.len(), 1);
    let props = tools[0]
        .get("inputSchema")
        .and_then(|s| s.get("properties"))
        .and_then(|p| p.as_object())
        .expect("properties");
    assert!(props.contains_key("required_field"));
    assert!(props.contains_key("optional_field"));
}
