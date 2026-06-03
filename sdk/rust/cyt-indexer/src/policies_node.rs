//! N-API bindings for policies, documents, and catalog I/O (mirrors policies_python.rs).

use crate::build::catalog_index_from_value;
use crate::catalog_builder::CatalogBuilder as RustCatalogBuilder;
use crate::policies::{self, policy_context_from_values, PolicyContext, ToolPolicy};
use crate::runtime_config;
use napi::bindgen_prelude::*;
use napi_derive::napi;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

/// N-API policy context (defaults live in [`PolicyContext::new`]).
#[napi(js_name = "PolicyContext")]
pub struct PolicyContextNapi {
    pub(crate) inner: PolicyContext,
}

#[napi]
impl PolicyContextNapi {
    #[napi(constructor)]
    pub fn new(system_policy: Option<String>, mcp_policy: Option<String>) -> Self {
        Self {
            inner: PolicyContext::with_overrides(
                system_policy.and_then(|s| ToolPolicy::from_str(&s)),
                mcp_policy.and_then(|s| ToolPolicy::from_str(&s)),
                HashMap::new(),
            ),
        }
    }

    #[napi(getter)]
    pub fn system_policy(&self) -> String {
        self.inner.system_policy.as_str().to_string()
    }

    #[napi(setter)]
    pub fn set_system_policy(&mut self, value: String) {
        if let Some(p) = ToolPolicy::from_str(&value) {
            self.inner.system_policy = p;
        }
    }

    #[napi(getter)]
    pub fn mcp_policy(&self) -> String {
        self.inner.mcp_policy.as_str().to_string()
    }

    #[napi(setter)]
    pub fn set_mcp_policy(&mut self, value: String) {
        if let Some(p) = ToolPolicy::from_str(&value) {
            self.inner.mcp_policy = p;
        }
    }

    #[napi(getter)]
    pub fn per_tool(&self) -> HashMap<String, String> {
        self.inner
            .per_tool
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().to_string()))
            .collect()
    }

    #[napi(setter)]
    pub fn set_per_tool(&mut self, value: HashMap<String, String>) {
        let mut per_tool = HashMap::new();
        for (k, v) in value {
            if let Some(p) = ToolPolicy::from_str(&v) {
                per_tool.insert(k, p);
            }
        }
        self.inner.per_tool = per_tool;
    }
}

pub(crate) fn ctx_from_napi(ctx: &PolicyContextNapi) -> &PolicyContext {
    &ctx.inner
}

pub(crate) fn ctx_from_js_object(ctx: PolicyContextJs) -> PolicyContext {
    let per_tool = ctx
        .per_tool
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(k, v)| ToolPolicy::from_str(&v).map(|p| (k, p)))
        .collect();
    PolicyContext::with_overrides(
        ctx.system_policy
            .as_deref()
            .and_then(ToolPolicy::from_str),
        ctx.mcp_policy.as_deref().and_then(ToolPolicy::from_str),
        per_tool,
    )
}

#[napi(object)]
pub struct PolicyContextJs {
    pub system_policy: Option<String>,
    pub mcp_policy: Option<String>,
    pub per_tool: Option<HashMap<String, String>>,
}

pub(crate) fn ctx_from_any(
    ctx: Option<Either<&PolicyContextNapi, PolicyContextJs>>,
) -> PolicyContext {
    match ctx {
        None => policy_context_from_values(&Value::Object(serde_json::Map::new())),
        Some(Either::A(napi)) => napi.inner.clone(),
        Some(Either::B(js)) => ctx_from_js_object(js),
    }
}

fn optional_path_set(paths: Option<Vec<String>>) -> Option<HashSet<String>> {
    paths.map(|items| items.into_iter().collect())
}

#[napi(js_name = "policyContextFromValues")]
pub fn policy_context_from_values_napi(config: Value) -> PolicyContextNapi {
    PolicyContextNapi {
        inner: policy_context_from_values(&config),
    }
}

#[napi]
pub fn effective_policy(ctx: &PolicyContextNapi, tool_id: String) -> String {
    policies::effective_policy(ctx_from_napi(ctx), &tool_id)
        .as_str()
        .to_string()
}

#[napi]
pub fn tool_pass_through(ctx: &PolicyContextNapi, tool_id: String) -> bool {
    policies::tool_pass_through(ctx_from_napi(ctx), &tool_id)
}

#[napi]
pub fn partition_catalog(
    data: Value,
    ctx: &PolicyContextNapi,
) -> Result<(Value, Value)> {
    let (proc, pinned) = policies::partition_catalog(&data, ctx_from_napi(ctx));
    Ok((proc, pinned))
}

#[napi]
pub fn merge_catalog(processed: Value, pinned: Value) -> Value {
    policies::merge_catalog(&processed, &pinned)
}

#[napi]
pub fn catalog_needs_partition(data: Value, ctx: &PolicyContextNapi) -> bool {
    policies::catalog_needs_partition(&data, ctx_from_napi(ctx))
}

#[napi]
pub fn catalog_needs_pruned_recompose(data: Value, ctx: &PolicyContextNapi) -> bool {
    policies::catalog_needs_pruned_recompose(&data, ctx_from_napi(ctx))
}

#[napi]
pub fn request_pass_through(ctx: &PolicyContextNapi, tools: Vec<Value>) -> bool {
    policies::request_pass_through(ctx_from_napi(ctx), &tools)
}

#[napi]
pub fn full_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::full_pass_through(ctx_from_napi(ctx))
}

#[napi]
pub fn is_decomposed_tool_root_chunk(item: Value) -> bool {
    policies::is_decomposed_tool_root_chunk(&item)
}

#[napi]
pub fn is_decomposed_optional_property_chunk(item: Value) -> bool {
    policies::is_decomposed_optional_property_chunk(&item)
}

#[napi]
pub fn filter_recompose_json_entries(
    json_list: Vec<Value>,
    ctx: &PolicyContextNapi,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Vec<String>>,
) -> Vec<Value> {
    policies::filter_recompose_json_entries(
        ctx_from_napi(ctx),
        &json_list,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        optional_path_set(llm_selected_paths).as_ref(),
    )
}

#[napi]
pub fn mitigate_empty_optional_properties(
    entries: Vec<Value>,
    catalog_index: Value,
    ctx: &PolicyContextNapi,
    post_rerank_scored: Option<Value>,
    pipeline: Vec<String>,
) -> Vec<Value> {
    let index = catalog_index_from_value(&catalog_index);
    policies::mitigate_empty_optional_properties(
        ctx_from_napi(ctx),
        &entries,
        &index,
        post_rerank_scored.as_ref(),
        &pipeline,
    )
}

#[napi]
pub fn drop_recomposed_tools_with_empty_properties(
    tools: Vec<Value>,
    catalog_index: Value,
    ctx: &PolicyContextNapi,
) -> Vec<Value> {
    let index = catalog_index_from_value(&catalog_index);
    policies::drop_recomposed_tools_with_empty_properties(ctx_from_napi(ctx), &tools, &index)
}

#[napi]
pub fn extract_json_catalog_document(item: Value) -> Option<String> {
    crate::documents::extract_json_catalog_document(&item)
}

#[napi]
pub fn extract_md_catalog_document(item: Value) -> Option<String> {
    crate::documents::extract_md_catalog_document(&item)
}

#[napi]
pub fn extract_document_text(item: Value) -> Option<String> {
    crate::documents::extract_document_text(&item)
}

#[napi]
pub fn extract_level_info(item: Value) -> Vec<String> {
    crate::documents::extract_level_info(&item)
}

#[napi]
pub fn write_catalog_index(
    index: Value,
    output_dir: Option<String>,
    prune: Option<bool>,
) -> Result<()> {
    let catalog = catalog_index_from_value(&index);
    crate::catalog_io::write_catalog_index_resolved(
        &catalog,
        output_dir.as_deref().map(std::path::Path::new),
        prune,
    )
    .map_err(Error::from_reason)
}

#[napi]
pub fn root_tool_id_from_chunk(item: Value) -> String {
    policies::root_tool_id_from_chunk(&item)
}

#[napi]
pub fn is_non_system_tool_id(tool_id: String) -> bool {
    policies::is_non_system_tool_id(&tool_id)
}

#[napi]
pub fn is_system_tool_id(tool_id: String) -> bool {
    policies::is_system_tool_id(&tool_id)
}

#[napi]
pub fn chunk_tool_id(item: Value) -> String {
    policies::chunk_tool_id(&item)
}

#[napi]
pub fn is_system_chunk(item: Value) -> bool {
    policies::is_system_chunk(&item)
}

#[napi]
pub fn is_non_system_chunk(item: Value) -> bool {
    policies::is_non_system_chunk(&item)
}

#[napi]
pub fn is_system_root_chunk(item: Value) -> bool {
    policies::is_system_root_chunk(&item)
}

#[napi]
pub fn is_mcp_root_chunk(item: Value) -> bool {
    policies::is_mcp_root_chunk(&item)
}

#[napi]
pub fn is_system_optional_chunk(item: Value) -> bool {
    policies::is_system_optional_chunk(&item)
}

#[napi]
pub fn is_mcp_optional_chunk(item: Value) -> bool {
    policies::is_mcp_optional_chunk(&item)
}

#[napi]
pub fn stash_system_tools(tools: Vec<Value>) -> Vec<Value> {
    policies::stash_system_tools(&tools)
}

#[napi]
pub fn restore_system_tools(stash: Vec<Value>) -> Vec<Value> {
    policies::restore_system_tools(&stash)
}

#[napi]
pub fn stash_mcp_tools(tools: Vec<Value>) -> Vec<Value> {
    policies::stash_mcp_tools(&tools)
}

#[napi]
pub fn restore_mcp_tools(stash: Vec<Value>) -> Vec<Value> {
    policies::restore_mcp_tools(&stash)
}

#[napi]
pub fn merge_tools_preserving_order(
    original: Vec<Value>,
    pruned_by_name: HashMap<String, Value>,
    stashed_by_name: HashMap<String, Value>,
) -> Vec<Value> {
    policies::merge_tools_preserving_order(&original, &pruned_by_name, &stashed_by_name)
}

#[napi(object)]
pub struct SplitAnthropicToolsResult {
    pub non_system: Vec<Value>,
    pub system: Vec<Value>,
}

#[napi]
pub fn split_anthropic_tools(tools: Vec<Value>) -> SplitAnthropicToolsResult {
    let (non_system, system) = policies::split_anthropic_tools(&tools);
    SplitAnthropicToolsResult {
        non_system,
        system,
    }
}

#[napi]
pub fn entries_for_policy(ctx: &PolicyContextNapi, all_entries: Vec<Value>) -> Vec<Value> {
    policies::entries_for_policy(ctx_from_napi(ctx), &all_entries)
}

#[napi]
pub fn tools_for_catalog(ctx: &PolicyContextNapi, tools: Vec<Value>) -> Vec<Value> {
    policies::tools_for_catalog(ctx_from_napi(ctx), &tools)
}

#[napi]
pub fn system_required_enum_values(data: Value) -> Vec<String> {
    policies::system_required_enum_values(&data)
        .into_iter()
        .collect()
}

#[napi]
pub fn mcp_required_enum_values(data: Value) -> Vec<String> {
    policies::mcp_required_enum_values(&data)
        .into_iter()
        .collect()
}

#[napi]
pub fn required_enum_values_by_tool(data: Value) -> HashMap<String, Vec<String>> {
    policies::required_enum_values_by_tool(&data)
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().collect()))
        .collect()
}

#[napi]
pub fn optional_leaf_survived_rerank(
    item: Value,
    ctx: &PolicyContextNapi,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Vec<String>>,
) -> bool {
    policies::optional_leaf_survived_rerank(
        ctx_from_napi(ctx),
        &item,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        optional_path_set(llm_selected_paths).as_ref(),
    )
}

#[napi]
pub fn needs_partition(ctx: &PolicyContextNapi) -> bool {
    policies::needs_partition(ctx_from_napi(ctx))
}

#[napi]
pub fn needs_pruned_recompose(ctx: &PolicyContextNapi) -> bool {
    policies::needs_pruned_recompose(ctx_from_napi(ctx))
}

#[napi]
pub fn system_tools_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::system_tools_pass_through(ctx_from_napi(ctx))
}

#[napi]
pub fn mcp_tools_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::mcp_tools_pass_through(ctx_from_napi(ctx))
}

#[napi]
pub fn anthropic_tool_is_system(tool: Value) -> bool {
    policies::anthropic_tool_is_system(&tool)
}

#[napi]
pub fn anthropic_tool_is_mcp(tool: Value) -> bool {
    policies::anthropic_tool_is_mcp(&tool)
}

#[napi]
pub fn direct_root_optional_chunks_for_tool(items: Vec<Value>, tool_id: String) -> Vec<Value> {
    policies::direct_root_optional_chunks_for_tool(&items, &tool_id)
}

#[napi]
pub fn root_chunk_properties_empty(item: Value) -> bool {
    policies::root_chunk_properties_empty(&item)
}

#[napi]
pub fn tool_id_has_empty_decomposed_root(catalog_index: Value, tool_id: String) -> bool {
    let index = catalog_index_from_value(&catalog_index);
    policies::tool_id_has_empty_decomposed_root(&index, &tool_id)
}

#[napi]
pub fn tool_id_had_empty_original_root_properties(
    catalog_index: Value,
    tool_id: String,
) -> bool {
    let index = catalog_index_from_value(&catalog_index);
    policies::tool_id_had_empty_original_root_properties(&index, &tool_id)
}

#[napi]
pub fn is_direct_root_optional_property_chunk(item: Value) -> bool {
    policies::is_direct_root_optional_property_chunk(&item)
}

#[napi(js_name = "CatalogBuilder")]
pub struct CatalogBuilderNapi {
    inner: RustCatalogBuilder,
}

#[napi]
impl CatalogBuilderNapi {
    #[napi(constructor)]
    pub fn new(memory_only: Option<bool>, output_dir: Option<String>) -> Self {
        let dir = output_dir.map(std::path::PathBuf::from);
        Self {
            inner: RustCatalogBuilder::new_with_options(memory_only, dir),
        }
    }

    #[napi]
    pub fn add_tool(&mut self, entry: Value) -> Result<()> {
        self.inner.add_tool(entry);
        Ok(())
    }

    #[napi]
    pub fn get_tool_info(&self, server_name: String, tool_name: String) -> Option<Value> {
        self.inner
            .get_tool_info(&server_name, &tool_name)
            .cloned()
    }

    #[napi]
    pub fn build_index(&mut self) -> CatalogIndexResult {
        let index = self.inner.build_index();
        CatalogIndexResult {
            tools: index.tools.clone(),
            files: index.files.clone(),
        }
    }

    #[napi]
    pub fn write_catalog(&mut self) -> Result<CatalogIndexResult> {
        let index = self
            .inner
            .write_catalog()
            .map_err(|e| Error::from_reason(e))?;
        Ok(CatalogIndexResult {
            tools: index.tools.clone(),
            files: index.files.clone(),
        })
    }

    #[napi]
    pub fn to_catalog_dict(&mut self, catalog_prefix: Option<String>) -> Value {
        match catalog_prefix {
            Some(prefix) => self.inner.to_catalog_dict_with_prefix(&prefix),
            None => self.inner.to_catalog_dict(),
        }
    }
}

#[napi(object)]
pub struct CatalogIndexResult {
    pub tools: Vec<Value>,
    pub files: HashMap<String, String>,
}
