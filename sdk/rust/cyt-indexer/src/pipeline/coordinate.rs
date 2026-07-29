//! Parallel BM25 coordinator for skills search + tool catalog pruning.

use std::thread;

use serde_json::{Value, json};

use super::skills::{SearchSkillsOptions, search_skills_and_select};
use super::tools::{PruneBm25Options, PruneRetrieveResult, prune_catalog_bm25_and_retrieve};
use crate::build::CatalogIndex;
use crate::policies::PolicyContext;

#[derive(Debug, Clone, Default)]
pub struct CoordinateBm25Options {
    pub skills: SearchSkillsOptions,
    pub tools: PruneBm25Options,
}

fn prune_tools_result_to_json(result: PruneRetrieveResult) -> Value {
    let decomposed: serde_json::Map<String, Value> = result
        .decomposed
        .into_iter()
        .map(|(k, v)| (k, Value::from(v)))
        .collect();
    let breakdown: serde_json::Map<String, Value> = result
        .decomposed_breakdown
        .into_iter()
        .map(|(stage, counts)| {
            let inner: serde_json::Map<String, Value> = counts
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

/// Run skills BM25 search and tool BM25 prune in parallel (CPU-bound only).
///
/// # Errors
///
/// Returns an error when either composite pipeline fails.
#[allow(clippy::too_many_arguments)]
pub fn coordinate_bm25_prune(
    skills_entries: &[Value],
    catalog_data: &Value,
    build_catalog: &Value,
    catalog_index: &CatalogIndex,
    query: &str,
    scoring_ctx: &PolicyContext,
    output_ctx: &PolicyContext,
    options: &CoordinateBm25Options,
) -> Result<Value, String> {
    let skills_entries = skills_entries.to_vec();
    let catalog_data = catalog_data.clone();
    let build_catalog = build_catalog.clone();
    let catalog_index = catalog_index.clone();
    let query = query.to_string();
    let scoring_ctx = scoring_ctx.clone();
    let output_ctx = output_ctx.clone();
    let skills_options = options.skills.clone();
    let tools_options = options.tools.clone();

    thread::scope(|scope| {
        let skills_query = query.clone();
        let tools_query = query;
        let skills_handle = scope.spawn(move || {
            search_skills_and_select(&skills_entries, &skills_query, &skills_options)
        });
        let tools_handle = scope.spawn(move || {
            prune_catalog_bm25_and_retrieve(
                &catalog_data,
                &build_catalog,
                &catalog_index,
                &tools_query,
                &scoring_ctx,
                &output_ctx,
                &tools_options,
            )
        });

        let skills = skills_handle
            .join()
            .map_err(|_| "skills BM25 coordinator thread panicked".to_string())??;
        let tools = tools_handle
            .join()
            .map_err(|_| "tools BM25 coordinator thread panicked".to_string())??;

        Ok(json!({
            "skills": skills,
            "tools": prune_tools_result_to_json(tools),
        }))
    })
}
