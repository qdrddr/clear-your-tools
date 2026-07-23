//! Composite tool-catalog BM25 prune + recompose + retrieve.

use std::collections::HashMap;

use serde_json::{Map, Value, json};

use crate::bm25_search::{ScoreCatalogOptions, score_catalog_in_place};
use crate::build::{CatalogIndex, catalog_tool_count};
use crate::policies::{
    PolicyContext, catalog_needs_partition, classify_optional_chunks_batch,
    classify_optional_chunks_batch_with_ctx, drop_recomposed_tools_with_empty_properties,
    full_pass_through, json_entries_for_recompose, merge_catalog, partition_catalog,
};
use crate::retrieve::{DecomposedCatalog, RetrieveOptions, retrieve_tools_from_catalog};
use crate::runtime_config;
use crate::tiktoken;

#[derive(Debug, Clone)]
pub struct PruneBm25Options {
    pub score_tool: f64,
    pub score_tool_enum: f64,
    pub prune_enums: bool,
    pub pipeline: Vec<String>,
}

impl Default for PruneBm25Options {
    fn default() -> Self {
        Self {
            score_tool: runtime_config::decomposed_score(),
            score_tool_enum: runtime_config::enum_score(),
            prune_enums: true,
            pipeline: vec!["bm25".to_string()],
        }
    }
}

#[derive(Debug, Clone)]
pub struct PruneRetrieveResult {
    pub tools: Vec<Value>,
    pub decomposed: HashMap<String, usize>,
    pub decomposed_breakdown: HashMap<String, HashMap<String, usize>>,
    pub optional_chunk_count_in: usize,
    pub optional_chunk_count_out: usize,
}

fn count_json_md(data: &Value) -> (usize, usize) {
    let json_n = data
        .get("json")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    let md_n = data.get("md").and_then(Value::as_array).map_or(0, Vec::len);
    (json_n, md_n)
}

fn breakdown_entry(data: &Value) -> HashMap<String, usize> {
    let (json_n, md_n) = count_json_md(data);
    HashMap::from([("json".into(), json_n), ("md".into(), md_n)])
}

fn count_optional_property_chunks(data: &Value, ctx: &PolicyContext) -> usize {
    let Some(items) = data.get("json").and_then(Value::as_array) else {
        return 0;
    };
    let dict_items: Vec<Value> = items
        .iter()
        .filter(|item| item.is_object())
        .cloned()
        .collect();
    if dict_items.is_empty() {
        return 0;
    }
    let (system_flags, mcp_flags) = classify_optional_chunks_batch_with_ctx(&dict_items, ctx);
    system_flags
        .iter()
        .zip(mcp_flags.iter())
        .filter(|(sys, mcp)| **sys || **mcp)
        .count()
}

fn recompose_catalog_data(
    data: &Value,
    build_catalog: &Value,
    pinned: Option<&Value>,
    catalog_index: &CatalogIndex,
    post_rerank_scored: Option<&Value>,
    pipeline: &[String],
    ctx: &PolicyContext,
) -> Value {
    let mut recompose = Map::new();
    recompose.insert(
        "json".into(),
        Value::Array(json_entries_for_recompose(
            data,
            pinned,
            build_catalog,
            post_rerank_scored,
            ctx,
            catalog_index,
            pipeline,
        )),
    );
    let md = post_rerank_scored
        .and_then(|v| v.get("md").and_then(Value::as_array))
        .or_else(|| data.get("md").and_then(Value::as_array))
        .cloned()
        .unwrap_or_default();
    recompose.insert("md".into(), Value::Array(md));
    for key in [
        "system_required_enum_values",
        "mcp_required_enum_values",
        "required_enum_values_by_tool",
    ] {
        if let Some(v) = data.get(key) {
            recompose.insert(key.into(), v.clone());
        } else if let Some(pinned_val) = pinned
            && let Some(v) = pinned_val.get(key)
        {
            recompose.insert(key.into(), v.clone());
        }
    }
    Value::Object(recompose)
}

fn prune_catalog_lists(
    data: &mut Value,
    json_threshold: f64,
    md_threshold: f64,
    prune_enums: bool,
) {
    if let Some(items) = data.get_mut("json").and_then(Value::as_array_mut) {
        items.retain(|item| {
            item.as_object()
                .and_then(|o| o.get("score"))
                .and_then(|s| {
                    s.as_str()
                        .and_then(|t| t.parse::<f64>().ok())
                        .or_else(|| s.as_f64())
                })
                .unwrap_or(0.0)
                >= json_threshold
        });
    }
    if prune_enums && let Some(items) = data.get_mut("md").and_then(Value::as_array_mut) {
        items.retain(|item| {
            item.as_object()
                .and_then(|o| o.get("score"))
                .and_then(|s| {
                    s.as_str()
                        .and_then(|t| t.parse::<f64>().ok())
                        .or_else(|| s.as_f64())
                })
                .unwrap_or(0.0)
                >= md_threshold
        });
    }
}

/// Partition, BM25 score, recompose, and retrieve tools in one call.
///
/// # Errors
///
/// Returns an error when BM25 scoring or retrieval fails.
pub fn prune_catalog_bm25_and_retrieve(
    catalog_data: &Value,
    build_catalog: &Value,
    catalog_index: &CatalogIndex,
    query: &str,
    scoring_ctx: &PolicyContext,
    output_ctx: &PolicyContext,
    options: &PruneBm25Options,
) -> Result<PruneRetrieveResult, String> {
    let optional_chunk_count_in = count_optional_property_chunks(catalog_data, scoring_ctx);

    let build_breakdown = breakdown_entry(catalog_data);
    let build_total = build_breakdown.get("json").copied().unwrap_or(0)
        + build_breakdown.get("md").copied().unwrap_or(0);

    let mut data = catalog_data.clone();
    let pinned = if !full_pass_through(scoring_ctx) && catalog_needs_partition(&data, scoring_ctx) {
        let (processed, pinned_val) = partition_catalog(&data, scoring_ctx);
        data = processed;
        pinned_val
    } else {
        json!({})
    };

    let (post_rerank_scored, _post_rerank) = if full_pass_through(scoring_ctx) {
        (None, None)
    } else {
        let score_options = ScoreCatalogOptions {
            prune_json_threshold: None,
            prune_md_threshold: None,
            prune_enums: false,
            ..ScoreCatalogOptions::default()
        };
        score_catalog_in_place(&mut data, query, &score_options)?;
        let scored = Some(data.clone());
        prune_catalog_lists(
            &mut data,
            options.score_tool,
            options.score_tool_enum,
            options.prune_enums,
        );
        (scored, Some(data.clone()))
    };

    if pinned.as_object().is_some_and(|o| !o.is_empty()) {
        data = merge_catalog(&data, &pinned);
    }

    let bm25_breakdown = breakdown_entry(&data);
    let bm25_total = bm25_breakdown.get("json").copied().unwrap_or(0)
        + bm25_breakdown.get("md").copied().unwrap_or(0);

    let pipeline = if options.pipeline.is_empty() {
        vec!["bm25".to_string()]
    } else {
        options.pipeline.clone()
    };

    let recompose_data = recompose_catalog_data(
        &data,
        build_catalog,
        Some(&pinned),
        catalog_index,
        post_rerank_scored.as_ref(),
        &pipeline,
        scoring_ctx,
    );

    let mut store = DecomposedCatalog::from_catalog_index(catalog_index);
    let retrieve_opts = RetrieveOptions {
        apply_decomposed_score_filter: false,
        ..RetrieveOptions::default()
    };
    let tools = retrieve_tools_from_catalog(
        output_ctx,
        &recompose_data,
        build_catalog,
        &mut store,
        &retrieve_opts,
    );
    let tools = drop_recomposed_tools_with_empty_properties(output_ctx, &tools, catalog_index);

    let optional_chunk_count_out = count_optional_property_chunks(&recompose_data, scoring_ctx);

    let mut decomposed_breakdown = HashMap::new();
    decomposed_breakdown.insert("build_index".into(), build_breakdown);
    decomposed_breakdown.insert("bm25".into(), bm25_breakdown);

    let mut decomposed = HashMap::new();
    decomposed.insert("build_index".into(), build_total);
    decomposed.insert("bm25".into(), bm25_total);

    Ok(PruneRetrieveResult {
        tools,
        decomposed,
        decomposed_breakdown,
        optional_chunk_count_in,
        optional_chunk_count_out,
    })
}

/// Recompose pruned catalog survivors and retrieve merged tool schemas in one call.
#[must_use]
#[allow(clippy::too_many_arguments)]
pub fn recompose_and_retrieve_tools(
    data: &Value,
    build_catalog: &Value,
    catalog_index: &CatalogIndex,
    post_rerank: Option<&Value>,
    post_rerank_scored: Option<&Value>,
    pinned: Option<&Value>,
    pipeline: &[String],
    scoring_ctx: &PolicyContext,
    output_ctx: &PolicyContext,
) -> Vec<Value> {
    let _ = post_rerank;
    let recompose_data = recompose_catalog_data(
        data,
        build_catalog,
        pinned,
        catalog_index,
        post_rerank_scored,
        pipeline,
        scoring_ctx,
    );

    let mut store = DecomposedCatalog::from_catalog_index(catalog_index);
    let retrieve_opts = RetrieveOptions {
        apply_decomposed_score_filter: false,
        ..RetrieveOptions::default()
    };
    let tools = retrieve_tools_from_catalog(
        output_ctx,
        &recompose_data,
        build_catalog,
        &mut store,
        &retrieve_opts,
    );
    drop_recomposed_tools_with_empty_properties(output_ctx, &tools, catalog_index)
}

/// Classify optional catalog chunks and optionally count tool JSON tokens.
///
/// # Errors
///
/// Returns an error when token counting fails.
pub fn classify_and_count_catalog(data: &Value, tools: Option<&[Value]>) -> Result<Value, String> {
    let json_items: Vec<Value> = data
        .get("json")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let dict_items: Vec<Value> = json_items
        .iter()
        .filter(|item| item.is_object())
        .cloned()
        .collect();
    let (system_flags, mcp_flags) = classify_optional_chunks_batch(&dict_items);
    let optional_chunk_count = system_flags
        .iter()
        .zip(mcp_flags.iter())
        .filter(|(sys, mcp)| **sys || **mcp)
        .count();

    let mut result = json!({
        "optional_chunk_count": optional_chunk_count,
        "system_optional": system_flags,
        "mcp_optional": mcp_flags,
        "catalog_tool_count": catalog_tool_count(data),
    });

    if let Some(tools) = tools {
        result["tokens"] = json!(tiktoken::count_json_tokens(&Value::Array(tools.to_vec()))?);
        result["tool_count"] = json!(tools.len());
    }

    Ok(result)
}
