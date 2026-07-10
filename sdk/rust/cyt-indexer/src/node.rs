#[path = "pageindex_node.rs"]
mod pageindex_node;

pub use pageindex_node::*;

use crate::build::{
    build_catalog_index as core_build_catalog_index, catalog_index_from_value,
    catalog_tool_count as core_catalog_tool_count,
};
use crate::catalog_builder::CatalogBuilder as RustCatalogBuilder;
use crate::paths::{self, collect_enums as paths_collect_enums};
use crate::policies::{
    self as policies, PolicyContext, parse_tool_policy, policy_context_from_values,
    tool_policy_strings,
};
use crate::retrieve::{
    DecomposedCatalog, ProcessGroupsOptions, RemovedChunksOptions, RetrieveOptions,
    build_process_groups_options, chunk_survivor_key, decomposed_catalog_from_value,
    load_catalog_from_dir, process_groups_options_from_fields, removed_chunks,
    resolve_build_catalog, retrieve_core as core_retrieve_core, retrieve_tools_from_catalog,
};
use crate::runtime_config;
use crate::tool_entries::{
    anthropic_tool_to_catalog_entry as core_anthropic_tool_to_catalog_entry,
    anthropic_tools_to_catalog_entries as core_anthropic_tools_to_catalog_entries,
    build_catalog_from_tools as core_build_catalog_from_tools,
    prepare_tool_entry as core_prepare_tool_entry,
    truncate_description as core_truncate_description,
};
use napi::bindgen_prelude::*;
use napi_derive::napi;
use serde_json::Value;
use std::collections::hash_map::RandomState;
use std::collections::{HashMap, HashSet};

#[napi(object)]
pub struct PolicyOptions {
    pub prune_optional_tools: Option<Vec<String>>,
    pub system_preserve: Option<Vec<String>>,
    pub mcp_preserve: Option<Vec<String>>,
    pub required_by_tool: Option<HashMap<String, Vec<String>>>,
    pub required_enum_values_by_tool: Option<HashMap<String, Vec<String>>>,
}

#[napi(object)]
pub struct PathConfigNapi {
    pub md_ext: String,
    pub json_ext: String,
    pub decomposed_prefix: String,
    pub decomposed_root: String,
    pub catalog_prefix: String,
    pub builder_memory_only: bool,
    pub default_catalog_dir: String,
    pub write_catalog_prune: bool,
}

const fn json_files_from_map(map: HashMap<String, Value>) -> DecomposedCatalog {
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
    #[must_use]
    pub fn new(json_files: Option<HashMap<String, Value>>) -> Self {
        Self {
            inner: DecomposedCatalog::from_json_files(json_files.unwrap_or_default()),
        }
    }

    /// # Errors
    /// Does not fail; invalid index shapes yield an empty decomposed catalog.
    #[napi(factory)]
    pub fn from_catalog_index(index: Value) -> Result<Self> {
        let index = Box::new(index);
        let idx = catalog_index_from_value(&index);
        Ok(Self {
            inner: DecomposedCatalog::from_catalog_index(&idx),
        })
    }

    #[napi(factory)]
    #[must_use]
    pub fn from_catalog_dict(data: Value) -> Self {
        let data = Box::new(data);
        Self {
            inner: DecomposedCatalog::from_catalog_dict(&data),
        }
    }

    #[napi]
    #[must_use]
    pub fn has_json(&self, key: String) -> bool {
        let key = key.into_boxed_str();
        self.inner.has_json(key.as_ref())
    }

    #[napi]
    #[must_use]
    pub fn get_json(&self, key: String) -> Option<Value> {
        let key = key.into_boxed_str();
        self.inner.get_json(key.as_ref()).cloned()
    }

    #[napi]
    #[must_use]
    pub fn resolve_key(&self, file_path: String) -> Option<String> {
        let file_path = file_path.into_boxed_str();
        self.inner.resolve_key(file_path.as_ref())
    }

    #[napi]
    #[must_use]
    pub fn to_json_files(&self) -> HashMap<String, Value> {
        self.inner.json_files().clone()
    }
}

/// # Errors
/// Returns an error when the tool count exceeds `u32::MAX`.
#[napi]
pub fn catalog_tool_count(data: Value) -> Result<u32> {
    let data = Box::new(data);
    u32::try_from(core_catalog_tool_count(&data))
        .map_err(|_| Error::from_reason("catalog tool count exceeds u32::MAX"))
}

/// # Errors
/// Does not fail; malformed tool entries are skipped during indexing.
#[napi]
pub fn build_catalog_index(tools: Vec<Value>, all_enums: Vec<Value>) -> Result<CatalogIndexResult> {
    let tools = tools.into_boxed_slice();
    let all_enums = all_enums.into_boxed_slice();
    let index = core_build_catalog_index(&tools, &all_enums);
    Ok(CatalogIndexResult {
        tools: index.tools,
        files: index.files,
    })
}

#[napi(object)]
pub struct AnthropicCatalogEntriesResult {
    pub entries: Vec<Value>,
    pub enums: Vec<Value>,
}

/// # Errors
/// Does not fail; malformed tool entries are skipped during indexing.
#[napi]
pub fn build_catalog_from_tools(tools: Vec<Value>) -> Result<CatalogIndexResult> {
    let tools = tools.into_boxed_slice();
    let index = core_build_catalog_from_tools(&tools);
    Ok(CatalogIndexResult {
        tools: index.tools,
        files: index.files,
    })
}

/// # Errors
/// Does not fail; invalid schema fragments are omitted from the entry.
#[napi]
pub fn prepare_tool_entry(
    server_name: String,
    name: String,
    description: String,
    input_schema: Value,
) -> Result<Value> {
    let server_name = server_name.into_boxed_str();
    let name = name.into_boxed_str();
    let description = description.into_boxed_str();
    let input_schema = Box::new(input_schema);
    Ok(core_prepare_tool_entry(
        server_name.as_ref(),
        name.as_ref(),
        description.as_ref(),
        &input_schema,
    ))
}

#[napi]
#[must_use]
pub fn anthropic_tool_to_catalog_entry(tool: Value) -> Option<Value> {
    let tool = Box::new(tool);
    core_anthropic_tool_to_catalog_entry(&tool)
}

#[napi]
#[must_use]
pub fn anthropic_tools_to_catalog_entries(tools: Vec<Value>) -> AnthropicCatalogEntriesResult {
    let tools = tools.into_boxed_slice();
    let (entries, enums) = core_anthropic_tools_to_catalog_entries(&tools);
    AnthropicCatalogEntriesResult { entries, enums }
}

#[napi]
#[must_use]
pub fn truncate_description(description: String, max_tokens: Option<u32>) -> String {
    let description = description.into_boxed_str();
    core_truncate_description(description.as_ref(), max_tokens.unwrap_or(60) as usize)
}

#[napi]
pub fn tool_policies() -> Vec<String> {
    tool_policy_strings()
        .into_iter()
        .map(str::to_string)
        .collect()
}

/// # Errors
/// Does not fail; missing catalog data is treated as empty.
#[napi]
pub fn retrieve_tools(
    data: Value,
    catalog: Value,
    apply_decomposed_score_filter: Option<bool>,
    preserve_values: Option<Vec<String>>,
    policy_ctx: Option<Either<&PolicyContextNapi, PolicyContextJs>>,
) -> Result<Vec<Value>> {
    let policy_ctx = ctx_from_any(policy_ctx);
    let survivor_data = if data.is_object() {
        data
    } else {
        Value::Object(serde_json::Map::new())
    };
    let catalog = Box::new(catalog);
    let build_catalog = resolve_build_catalog(&catalog, &survivor_data);
    let mut store = decomposed_catalog_from_value(&catalog);
    let preserve_set = preserve_values;
    let process_groups =
        build_process_groups_options(&policy_ctx, &build_catalog, &store, preserve_set);
    let opts = RetrieveOptions {
        apply_decomposed_score_filter: apply_decomposed_score_filter.unwrap_or(false),
        process_groups,
    };
    Ok(retrieve_tools_from_catalog(
        &policy_ctx,
        &survivor_data,
        &build_catalog,
        &mut store,
        &opts,
    ))
}

/// # Errors
/// Does not fail; malformed survivor or store entries are skipped.
#[napi]
pub fn retrieve_core(
    data: Value,
    store_json_files: HashMap<String, Value, RandomState>,
    survivor_json_files: HashMap<String, Value, RandomState>,
    apply_decomposed_score_filter: Option<bool>,
    policy_options: Option<PolicyOptions>,
) -> Result<Vec<Value>> {
    let mut store = json_files_from_map(store_json_files);
    let survivor = json_files_from_map(survivor_json_files);
    let data = Box::new(data);
    let opts = RetrieveOptions {
        apply_decomposed_score_filter: apply_decomposed_score_filter.unwrap_or(false),
        process_groups: process_groups_from_policy(policy_options),
    };
    Ok(core_retrieve_core(&data, &mut store, &survivor, &opts))
}

/// # Errors
/// Returns an error when the catalog directory cannot be read or parsed.
#[napi]
pub fn load_catalog(dir_path: String) -> Result<Value> {
    let dir_path = dir_path.into_boxed_str();
    load_catalog_from_dir(dir_path.as_ref()).map_err(Error::from_reason)
}

#[napi(js_name = "chunkSurvivorKey")]
#[must_use]
pub fn chunk_survivor_key_napi(item: Value, section: String) -> Option<String> {
    let item = Box::new(item);
    let section = section.into_boxed_str();
    chunk_survivor_key(&item, section.as_ref())
}

/// # Errors
/// Does not fail; missing catalog sections are treated as empty.
#[napi(js_name = "removedChunks")]
pub fn removed_chunks_napi(
    full_catalog: Value,
    surviving: Value,
    apply_decomposed_score_filter: Option<bool>,
) -> Result<Value> {
    let full_catalog = Box::new(full_catalog);
    let surviving = Box::new(surviving);
    Ok(removed_chunks(
        &full_catalog,
        &surviving,
        &RemovedChunksOptions {
            apply_decomposed_score_filter: apply_decomposed_score_filter.unwrap_or(false),
        },
    ))
}

/// # Errors
/// Does not fail; updates in-process path configuration.
#[napi]
pub fn configure_path_constants(config: PathConfigNapi) -> Result<()> {
    let defaults = paths::PathConfig::default();
    paths::configure(paths::PathConfig {
        md_ext: config.md_ext,
        json_ext: config.json_ext,
        decomposed_prefix: config.decomposed_prefix,
        decomposed_root: std::path::PathBuf::from(config.decomposed_root),
        skills_decomposed_prefix: defaults.skills_decomposed_prefix,
        skills_decomposed_root: defaults.skills_decomposed_root,
        catalog_prefix: config.catalog_prefix,
        builder_memory_only: config.builder_memory_only,
        default_catalog_dir: std::path::PathBuf::from(config.default_catalog_dir),
        write_catalog_prune: config.write_catalog_prune,
    });
    Ok(())
}

#[napi]
#[must_use]
pub fn catalog_prefix() -> String {
    paths::catalog_prefix()
}

/// # Errors
/// Does not fail; updates in-process runtime configuration.
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

#[napi(js_name = "decomposedScore")]
#[must_use]
pub fn decomposed_score_napi() -> f64 {
    runtime_config::decomposed_score()
}

#[napi(js_name = "enumScore")]
#[must_use]
pub fn enum_score_napi() -> f64 {
    runtime_config::enum_score()
}

#[napi(js_name = "rerankScore")]
#[must_use]
pub fn rerank_score_napi() -> f64 {
    runtime_config::rerank_score()
}

#[napi(js_name = "emptyOptionalFallbackK")]
#[must_use]
pub fn empty_optional_fallback_k_napi() -> u32 {
    u32::try_from(runtime_config::empty_optional_fallback_k()).unwrap_or(u32::MAX)
}

#[napi(js_name = "runtimeDefaultSystemPolicy")]
#[must_use]
pub fn runtime_default_system_policy_napi() -> String {
    runtime_config::default_system_policy()
}

#[napi(js_name = "runtimeDefaultMcpPolicy")]
#[must_use]
pub fn runtime_default_mcp_policy_napi() -> String {
    runtime_config::default_mcp_policy()
}

/// # Errors
/// Does not fail; malformed catalog shapes yield an empty build catalog.
#[napi(js_name = "resolveBuildCatalog")]
pub fn resolve_build_catalog_napi(catalog: Value, survivor: Value) -> Result<Value> {
    let catalog = Box::new(catalog);
    let survivor = Box::new(survivor);
    let build = if catalog.get("tools").is_some() {
        catalog_index_from_value(&catalog).to_catalog_dict()
    } else {
        resolve_build_catalog(&catalog, &survivor)
    };
    Ok(build)
}

/// # Errors
/// Does not fail; missing catalog sections count as zero tools.
#[napi(js_name = "retrieveCatalogToolCount")]
pub fn retrieve_catalog_tool_count_napi(data: Value) -> Result<i64> {
    let data = Box::new(data);
    i64::try_from(core_catalog_tool_count(&data))
        .map_err(|_| Error::from_reason("catalog tool count overflow"))
}

#[napi]
#[must_use]
pub fn path_builder_memory_only() -> bool {
    paths::builder_memory_only()
}

#[napi]
#[must_use]
pub fn path_default_catalog_dir() -> String {
    paths::default_catalog_dir().to_string_lossy().into_owned()
}

#[napi]
#[must_use]
pub fn path_write_catalog_prune() -> bool {
    paths::write_catalog_prune()
}

/// # Errors
/// Does not fail; invalid index shapes yield an empty catalog dict.
#[napi]
pub fn catalog_index_to_catalog_dict(
    index: Value,
    catalog_prefix: Option<String>,
) -> Result<Value> {
    let index = Box::new(index);
    let idx = catalog_index_from_value(&index);
    let val = catalog_prefix.map_or_else(
        || idx.to_catalog_dict(),
        |prefix| {
            let prefix = prefix.into_boxed_str();
            idx.to_catalog_dict_with_prefix(prefix.as_ref())
        },
    );
    Ok(val)
}

/// # Errors
/// Does not fail; invalid index shapes yield empty metadata.
#[napi]
pub fn catalog_index_tool_schema_metadata(index: Value) -> Result<Value> {
    let index = Box::new(index);
    let idx = catalog_index_from_value(&index);
    Ok(idx.tool_schema_metadata())
}

#[napi]
#[must_use]
pub fn get_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[napi]
#[must_use]
pub fn md_ext() -> String {
    paths::md_ext()
}

#[napi]
#[must_use]
pub fn json_ext() -> String {
    paths::json_ext()
}

#[napi]
#[must_use]
pub fn decomposed_prefix() -> String {
    paths::decomposed_prefix()
}

#[napi]
#[must_use]
pub fn decomposed_root() -> String {
    paths::decomposed_root().to_string_lossy().into_owned()
}

#[napi]
#[must_use]
pub fn to_decomposed_key(file_path: String) -> Option<String> {
    let file_path = file_path.into_boxed_str();
    paths::to_decomposed_key(file_path.as_ref())
}

#[napi]
#[must_use]
pub fn tool_id_from_decomposed_rel(rel_path: String) -> String {
    let rel_path = rel_path.into_boxed_str();
    paths::tool_id_from_decomposed_rel(rel_path.as_ref())
}

#[napi]
#[must_use]
pub fn get_root_tool_key(file_path: String) -> Option<String> {
    let file_path = file_path.into_boxed_str();
    paths::get_root_tool_key(file_path.as_ref())
}

#[napi]
#[must_use]
pub fn collect_enums(schema: Value) -> Vec<Value> {
    let schema = Box::new(schema);
    paths_collect_enums(&schema)
}

include!("policies_node.rs");
include!("tokens_node.rs");
include!("bm25_search_node.rs");
include!("pipeline_node.rs");
include!("cache_node.rs");
