// N-API bindings for composite pipeline APIs (included from `node.rs`).

use crate::pipeline::{
    PruneBm25Options, PruneRetrieveResult, SearchSkillsOptions, build_skill_node_catalog,
    classify_and_count_catalog, prune_catalog_bm25_and_retrieve, search_skills_and_select,
};
use serde_json::Map;

fn prune_options_from_value(opt: Option<Value>) -> PruneBm25Options {
    let mut opts = PruneBm25Options::default();
    let Some(v) = opt else {
        return opts;
    };
    let Some(obj) = v.as_object() else {
        return opts;
    };
    if let Some(x) = obj.get("score_tool").and_then(Value::as_f64) {
        opts.score_tool = x;
    }
    if let Some(x) = obj.get("score_tool_enum").and_then(Value::as_f64) {
        opts.score_tool_enum = x;
    }
    if let Some(x) = obj.get("prune_enums").and_then(Value::as_bool) {
        opts.prune_enums = x;
    }
    if let Some(arr) = obj.get("pipeline").and_then(Value::as_array) {
        opts.pipeline = arr
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
    }
    opts
}

fn search_options_from_value(opt: Option<Value>) -> SearchSkillsOptions {
    let mut opts = SearchSkillsOptions::default();
    let Some(v) = opt else {
        return opts;
    };
    let Some(obj) = v.as_object() else {
        return opts;
    };
    if let Some(x) = obj.get("threshold").and_then(Value::as_f64) {
        opts.threshold = x;
    }
    if let Some(x) = obj.get("max_tokens").and_then(Value::as_i64) {
        opts.max_tokens = x;
    }
    if let Some(x) = obj.get("frontmatter_upper_limit").and_then(Value::as_f64) {
        opts.frontmatter_upper_limit = Some(x);
    }
    if let Some(x) = obj.get("item_kind").and_then(Value::as_str) {
        opts.item_kind = x.to_string();
    }
    opts
}

fn prune_result_to_value(result: PruneRetrieveResult) -> Value {
    let decomposed: Map<String, Value> = result
        .decomposed
        .into_iter()
        .map(|(k, v)| (k, Value::from(v)))
        .collect();
    let breakdown: Map<String, Value> = result
        .decomposed_breakdown
        .into_iter()
        .map(|(stage, counts)| {
            let inner: Map<String, Value> = counts
                .into_iter()
                .map(|(k, v)| (k, Value::from(v)))
                .collect();
            (stage, Value::Object(inner))
        })
        .collect();
    json!({
        "tools": result.tools,
        "decomposed": decomposed,
        "decomposed_breakdown": breakdown,
        "optional_chunk_count_in": result.optional_chunk_count_in,
        "optional_chunk_count_out": result.optional_chunk_count_out,
    })
}

/// Partition, BM25-score, recompose, and retrieve tools in one call.
///
/// # Errors
///
/// Returns an error when catalog shapes or retrieval fail.
#[napi(js_name = "pruneCatalogBm25AndRetrieve")]
#[allow(clippy::too_many_arguments)]
pub fn prune_catalog_bm25_and_retrieve_napi(
    catalog_data: Value,
    build_catalog: Value,
    catalog_index: Value,
    query: String,
    scoring_ctx: &PolicyContextNapi,
    output_ctx: &PolicyContextNapi,
    options: Option<Value>,
) -> Result<Value> {
    let catalog_data = Box::new(catalog_data);
    let build_catalog = Box::new(build_catalog);
    let catalog_index = Box::new(catalog_index);
    let query = query.into_boxed_str();
    let index = catalog_index_from_value(&catalog_index);
    let opts = prune_options_from_value(options);
    let result = prune_catalog_bm25_and_retrieve(
        &catalog_data,
        &build_catalog,
        &index,
        query.as_ref(),
        ctx_from_napi(scoring_ctx),
        ctx_from_napi(output_ctx),
        &opts,
    )
    .map_err(Error::from_reason)?;
    Ok(prune_result_to_value(result))
}

/// Classify optional chunks and optionally count tool tokens.
///
/// # Errors
///
/// Returns an error when catalog classification fails.
#[napi(js_name = "classifyAndCountCatalog")]
pub fn classify_and_count_catalog_napi(
    catalog_data: Value,
    tools: Option<Value>,
) -> Result<Value> {
    let catalog_data = Box::new(catalog_data);
    let tools_box = tools.map(Box::new);
    let tools_slice = tools_box
        .as_ref()
        .and_then(|v| v.as_array())
        .map(std::vec::Vec::as_slice);
    classify_and_count_catalog(&catalog_data, tools_slice).map_err(Error::from_reason)
}

/// BM25 skill search with optional budget selection.
///
/// # Errors
///
/// Returns an error when search or selection fails.
#[napi(js_name = "searchSkillsAndSelect")]
pub fn search_skills_and_select_napi(
    entries: Value,
    query: String,
    options: Option<Value>,
) -> Result<Value> {
    let entries = Box::new(entries);
    let query = query.into_boxed_str();
    let arr = entries.as_array().cloned().unwrap_or_default();
    let opts = search_options_from_value(options);
    search_skills_and_select(&arr, query.as_ref(), &opts).map_err(Error::from_reason)
}

/// Batch-load rerankable node bodies for skill entries.
///
/// # Errors
///
/// Returns an error when node catalog building fails.
#[napi(js_name = "buildSkillNodeCatalog")]
pub fn build_skill_node_catalog_napi(entries: Value) -> Result<Value> {
    let entries = Box::new(entries);
    let arr = entries.as_array().cloned().unwrap_or_default();
    let items =
        build_skill_node_catalog(&arr).map_err(Error::from_reason)?;
    Ok(Value::Array(items))
}
