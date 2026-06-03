#[path = "policies_node.rs"]
mod policies_node;

pub use policies_node::{
    CatalogBuilderNapi, CatalogIndexResult, PolicyContextJs, PolicyContextNapi, SplitAnthropicToolsResult,
};

use crate::build::{
    build_catalog_index as core_build_catalog_index,
    catalog_index_from_value, catalog_tool_count as core_catalog_tool_count,
};
use crate::paths::{self, collect_enums as paths_collect_enums};
use crate::policies::tool_policy_strings;
use crate::retrieve::{
    build_process_groups_options, decomposed_catalog_from_value, load_catalog_from_dir,
    process_groups_options_from_fields, retrieve_core as core_retrieve_core, DecomposedCatalog,
    ProcessGroupsOptions, RetrieveOptions,
};
use crate::runtime_config;
use napi::bindgen_prelude::*;
use napi_derive::napi;
use policies_node::ctx_from_any;
use serde_json::Value;
use std::collections::HashMap;

#[napi(object)]
pub struct PolicyOptions {
    pub prune_optional_tools: Option<Vec<String>>,
    pub system_preserve: Option<Vec<String>>,
    pub mcp_preserve: Option<Vec<String>>,
    pub required_by_tool: Option<HashMap<String, Vec<String>>>,
    pub required_enum_values_by_tool: Option<HashMap<String, Vec<String>>>,
}

fn json_files_from_map(map: HashMap<String, Value>) -> DecomposedCatalog {
    DecomposedCatalog::from_json_files(map)
}

fn process_groups_from_policy(policy: Option<PolicyOptions>) -> ProcessGroupsOptions {
    let Some(policy) = policy else {
        return ProcessGroupsOptions::default();
    };
    process_groups_options_from_fields(
        policy.system_preserve,
        policy.mcp_preserve,
        policy.required_by_tool,
        policy.required_enum_values_by_tool,
        policy.prune_optional_tools,
    )
}

/// In-memory decomposed catalog JSON (backed by Rust [`DecomposedCatalog`]).
#[napi(js_name = "DecomposedCatalog")]
pub struct DecomposedCatalogNapi {
    pub(crate) inner: DecomposedCatalog,
}

#[napi]
impl DecomposedCatalogNapi {
    #[napi(constructor)]
    pub fn new(json_files: Option<HashMap<String, Value>>) -> Self {
        Self {
            inner: DecomposedCatalog::from_json_files(json_files.unwrap_or_default()),
        }
    }

    #[napi(factory)]
    pub fn from_catalog_index(index: Value) -> Result<Self> {
        let idx = catalog_index_from_value(&index);
        Ok(Self {
            inner: DecomposedCatalog::from_catalog_index(&idx),
        })
    }

    #[napi(factory)]
    pub fn from_catalog_dict(data: Value) -> Self {
        Self {
            inner: DecomposedCatalog::from_catalog_dict(&data),
        }
    }

    #[napi]
    pub fn has_json(&self, key: String) -> bool {
        self.inner.has_json(&key)
    }

    #[napi]
    pub fn get_json(&self, key: String) -> Option<Value> {
        self.inner.get_json(&key).cloned()
    }

    #[napi]
    pub fn resolve_key(&self, file_path: String) -> Option<String> {
        self.inner.resolve_key(&file_path)
    }

    #[napi]
    pub fn to_json_files(&self) -> HashMap<String, Value> {
        self.inner.json_files().clone()
    }
}

#[napi]
pub fn catalog_tool_count(data: Value) -> Result<u32> {
    Ok(core_catalog_tool_count(&data) as u32)
}

#[napi]
pub fn build_catalog_index(tools: Vec<Value>, all_enums: Vec<Value>) -> Result<CatalogIndexResult> {
    let index = core_build_catalog_index(&tools, &all_enums);
    Ok(CatalogIndexResult {
        tools: index.tools,
        files: index.files,
    })
}

#[napi]
pub fn tool_policies() -> Vec<String> {
    tool_policy_strings()
        .into_iter()
        .map(str::to_string)
        .collect()
}

#[napi]
pub fn retrieve_tools(
    data: Value,
    catalog: Value,
    apply_decomposed_score_filter: Option<bool>,
    preserve_values: Option<Vec<String>>,
    policy_ctx: Option<Either<&PolicyContextNapi, PolicyContextJs>>,
) -> Result<Vec<Value>> {
    let policy_ctx = ctx_from_any(policy_ctx);
    let store = decomposed_catalog_from_value(&catalog);
    let catalog_dict = if data.is_object() {
        data
    } else {
        Value::Object(serde_json::Map::new())
    };
    let survivor = DecomposedCatalog::from_catalog_dict(&catalog_dict);
    let preserve_set = preserve_values.map(|items| items.into_iter().collect());
    let process_groups =
        build_process_groups_options(&policy_ctx, &catalog_dict, &store, preserve_set);
    let mut store_mut = store;
    let opts = RetrieveOptions {
        apply_decomposed_score_filter: apply_decomposed_score_filter.unwrap_or(true),
        process_groups,
    };
    Ok(core_retrieve_core(
        &catalog_dict,
        &mut store_mut,
        &survivor,
        &opts,
    ))
}

#[napi]
pub fn retrieve_core(
    data: Value,
    store_json_files: HashMap<String, Value>,
    survivor_json_files: HashMap<String, Value>,
    apply_decomposed_score_filter: Option<bool>,
    policy_options: Option<PolicyOptions>,
) -> Result<Vec<Value>> {
    let mut store = json_files_from_map(store_json_files);
    let survivor = json_files_from_map(survivor_json_files);
    let opts = RetrieveOptions {
        apply_decomposed_score_filter: apply_decomposed_score_filter.unwrap_or(true),
        process_groups: process_groups_from_policy(policy_options),
    };
    Ok(core_retrieve_core(&data, &mut store, &survivor, &opts))
}

#[napi]
pub fn load_catalog(dir_path: String) -> Result<Value> {
    load_catalog_from_dir(&dir_path).map_err(|e| Error::from_reason(e.to_string()))
}

#[napi]
pub fn configure_path_constants(
    md_ext: String,
    json_ext: String,
    decomposed_prefix: String,
    decomposed_root: String,
    catalog_prefix: String,
    builder_memory_only: bool,
    default_catalog_dir: String,
    write_catalog_prune: bool,
) -> Result<()> {
    paths::configure(paths::PathConfig {
        md_ext,
        json_ext,
        decomposed_prefix,
        decomposed_root: std::path::PathBuf::from(decomposed_root),
        catalog_prefix,
        builder_memory_only,
        default_catalog_dir: std::path::PathBuf::from(default_catalog_dir),
        write_catalog_prune,
    });
    Ok(())
}

#[napi]
pub fn catalog_prefix() -> String {
    paths::catalog_prefix()
}

#[napi]
pub fn configure_runtime_defaults(
    decomposed_score: f64,
    enum_score: f64,
    rerank_score: f64,
    empty_optional_fallback_k: u32,
    default_system_policy: String,
    default_mcp_policy: String,
) -> Result<()> {
    runtime_config::configure(runtime_config::RuntimeConfig {
        decomposed_score,
        enum_score,
        rerank_score,
        empty_optional_fallback_k: empty_optional_fallback_k as usize,
        default_system_policy,
        default_mcp_policy,
    });
    Ok(())
}

#[napi]
#[allow(non_snake_case)]
pub fn decomposedScore() -> f64 {
    runtime_config::decomposed_score()
}

#[napi]
#[allow(non_snake_case)]
pub fn enumScore() -> f64 {
    runtime_config::enum_score()
}

#[napi]
#[allow(non_snake_case)]
pub fn rerankScore() -> f64 {
    runtime_config::rerank_score()
}

#[napi]
#[allow(non_snake_case)]
pub fn emptyOptionalFallbackK() -> u32 {
    runtime_config::empty_optional_fallback_k() as u32
}

#[napi]
pub fn path_builder_memory_only() -> bool {
    paths::builder_memory_only()
}

#[napi]
pub fn path_default_catalog_dir() -> String {
    paths::default_catalog_dir().to_string_lossy().into_owned()
}

#[napi]
pub fn path_write_catalog_prune() -> bool {
    paths::write_catalog_prune()
}

#[napi]
pub fn catalog_index_to_catalog_dict(
    index: Value,
    catalog_prefix: Option<String>,
) -> Result<Value> {
    let idx = catalog_index_from_value(&index);
    let val = match catalog_prefix {
        Some(prefix) => idx.to_catalog_dict_with_prefix(&prefix),
        None => idx.to_catalog_dict(),
    };
    Ok(val)
}

#[napi]
pub fn md_ext() -> String {
    paths::md_ext()
}

#[napi]
pub fn json_ext() -> String {
    paths::json_ext()
}

#[napi]
pub fn decomposed_prefix() -> String {
    paths::decomposed_prefix()
}

#[napi]
pub fn decomposed_root() -> String {
    paths::decomposed_root().to_string_lossy().into_owned()
}

#[napi]
pub fn to_decomposed_key(file_path: String) -> Option<String> {
    paths::to_decomposed_key(&file_path)
}

#[napi]
pub fn tool_id_from_decomposed_rel(rel_path: String) -> String {
    paths::tool_id_from_decomposed_rel(&rel_path)
}

#[napi]
pub fn get_root_tool_key(file_path: String) -> Option<String> {
    paths::get_root_tool_key(&file_path)
}

#[napi]
pub fn collect_enums(schema: Value) -> Vec<Value> {
    paths_collect_enums(&schema)
}
