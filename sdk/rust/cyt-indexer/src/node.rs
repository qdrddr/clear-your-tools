use crate::build::{
    build_catalog_index as core_build_catalog_index,
    catalog_tool_count as core_catalog_tool_count,
};
use crate::retrieve::{
    load_catalog_from_dir, retrieve_core as core_retrieve_core, DecomposedCatalog,
    ProcessGroupsOptions, RetrieveOptions,
};
use napi::bindgen_prelude::*;
use napi_derive::napi;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[napi(object)]
pub struct CatalogIndexResult {
    pub tools: Vec<Value>,
    pub files: HashMap<String, String>,
}

#[napi(object)]
pub struct PolicyOptions {
    pub prune_optional_tools: Option<Vec<String>>,
    pub system_preserve: Option<Vec<String>>,
    pub mcp_preserve: Option<Vec<String>>,
    pub required_by_tool: Option<HashMap<String, Vec<String>>>,
}

fn json_files_from_map(map: HashMap<String, Value>) -> DecomposedCatalog {
    DecomposedCatalog::from_json_files(map)
}

fn process_groups_from_policy(policy: Option<PolicyOptions>) -> ProcessGroupsOptions {
    let Some(policy) = policy else {
        return ProcessGroupsOptions::default();
    };
    let required_by_tool = policy
        .required_by_tool
        .unwrap_or_default()
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().collect::<HashSet<_>>()))
        .collect();
    ProcessGroupsOptions {
        system_preserve: policy
            .system_preserve
            .map(|items| items.into_iter().collect()),
        mcp_preserve: policy.mcp_preserve.map(|items| items.into_iter().collect()),
        required_by_tool,
        prune_optional_tools: policy.prune_optional_tools.unwrap_or_default().into_iter().collect(),
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
