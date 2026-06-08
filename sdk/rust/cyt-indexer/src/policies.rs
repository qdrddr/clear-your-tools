//! System vs MCP tool policies for catalog pruning (rerank / llm).
//! Port of `src/cyt/pruners/policies.py`.

use crate::build::CatalogIndex;
use crate::json_util::value_to_string;
use crate::runtime_config;
use crate::paths::{
    collect_enums, decomposed_prefix, decomposed_root, get_root_tool_key, json_ext,
    to_decomposed_key, tool_id_from_decomposed_rel,
};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::path::Path;

const ALWAYS_INCLUDE: &str = "always_include";
const PRUNE_OPTIONAL: &str = "prune_optional";
const PRUNE_ALL: &str = "prune_all";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ToolPolicy {
    AlwaysInclude,
    PruneOptional,
    PruneAll,
}

/// Canonical policy string literals (for host language typing / validation).
pub fn tool_policy_strings() -> [&'static str; 3] {
    [ALWAYS_INCLUDE, PRUNE_OPTIONAL, PRUNE_ALL]
}

impl ToolPolicy {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            ALWAYS_INCLUDE => Some(Self::AlwaysInclude),
            PRUNE_OPTIONAL => Some(Self::PruneOptional),
            PRUNE_ALL => Some(Self::PruneAll),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::AlwaysInclude => ALWAYS_INCLUDE,
            Self::PruneOptional => PRUNE_OPTIONAL,
            Self::PruneAll => PRUNE_ALL,
        }
    }
}

impl Default for ToolPolicy {
    fn default() -> Self {
        Self::PruneOptional
    }
}

#[derive(Debug, Clone, Default)]
pub struct PolicyContext {
    pub system_policy: ToolPolicy,
    pub mcp_policy: ToolPolicy,
    pub per_tool: HashMap<String, ToolPolicy>,
}

impl PolicyContext {
    /// Defaults from [`runtime_config`] (overridable by the host app before use).
    pub fn new() -> Self {
        let system = runtime_config::default_system_policy();
        let mcp = runtime_config::default_mcp_policy();
        Self {
            system_policy: ToolPolicy::from_str(&system).unwrap_or(ToolPolicy::PruneOptional),
            mcp_policy: ToolPolicy::from_str(&mcp).unwrap_or(ToolPolicy::PruneAll),
            per_tool: HashMap::new(),
        }
    }

    /// Start from [`Self::new`] and apply optional overrides (used by Python/Node bindings).
    pub fn with_overrides(
        system_policy: Option<ToolPolicy>,
        mcp_policy: Option<ToolPolicy>,
        per_tool: HashMap<String, ToolPolicy>,
    ) -> Self {
        let mut ctx = Self::new();
        if let Some(s) = system_policy {
            ctx.system_policy = s;
        }
        if let Some(m) = mcp_policy {
            ctx.mcp_policy = m;
        }
        ctx.per_tool = per_tool;
        ctx
    }
}

/// Apply pruning policies from config JSON.
///
/// Precedence: `pruning.policy.system_tool` / `mcp_tool`, then legacy
/// `defaults.system_tool_policy` / `mcp_tool_policy`, plus `pruning.per_tool`.
pub fn policy_context_from_values(config: &Value) -> PolicyContext {
    let mut ctx = PolicyContext::new();
    let mut system_from_pruning = false;
    let mut mcp_from_pruning = false;
    if let Some(policy) = config
        .get("pruning")
        .and_then(Value::as_object)
        .and_then(|p| p.get("policy"))
        .and_then(Value::as_object)
    {
        if let Some(s) = policy
            .get("system_tool")
            .and_then(Value::as_str)
            .and_then(ToolPolicy::from_str)
        {
            ctx.system_policy = s;
            system_from_pruning = true;
        }
        if let Some(m) = policy
            .get("mcp_tool")
            .and_then(Value::as_str)
            .and_then(ToolPolicy::from_str)
        {
            ctx.mcp_policy = m;
            mcp_from_pruning = true;
        }
    }
    if let Some(defaults) = config.get("defaults").and_then(Value::as_object) {
        if !system_from_pruning {
            if let Some(s) = defaults
                .get("system_tool_policy")
                .and_then(Value::as_str)
                .and_then(ToolPolicy::from_str)
            {
                ctx.system_policy = s;
            }
        }
        if !mcp_from_pruning {
            if let Some(m) = defaults
                .get("mcp_tool_policy")
                .and_then(Value::as_str)
                .and_then(ToolPolicy::from_str)
            {
                ctx.mcp_policy = m;
            }
        }
    }
    if let Some(per_tool) = config
        .get("pruning")
        .and_then(Value::as_object)
        .and_then(|p| p.get("per_tool"))
        .and_then(Value::as_object)
    {
        for (tool_id, policy) in per_tool {
            if let Some(p) = policy.as_str().and_then(ToolPolicy::from_str) {
                ctx.per_tool.insert(tool_id.clone(), p);
            }
        }
    }
    ctx
}

/// Parse `TOOL=POLICY` (e.g. `Agent=always_include`).
pub fn parse_tool_policy_pair(s: &str) -> Result<(String, ToolPolicy), String> {
    let (tool_id, policy_str) = s
        .split_once('=')
        .ok_or_else(|| format!("expected TOOL=POLICY, got: {s}"))?;
    let tool_id = tool_id.trim();
    if tool_id.is_empty() {
        return Err(format!("expected TOOL=POLICY, got: {s}"));
    }
    let policy = ToolPolicy::from_str(policy_str.trim())
        .ok_or_else(|| format!("invalid policy for {tool_id}: {policy_str}"))?;
    Ok((tool_id.to_string(), policy))
}

/// Load per-tool overrides from a JSON object (`{"Agent": "always_include", ...}`).
pub fn per_tool_policies_from_value(val: &Value) -> Result<HashMap<String, ToolPolicy>, String> {
    let Some(map) = val.as_object() else {
        return Err("per-tool policies must be a JSON object".into());
    };
    let mut out = HashMap::new();
    for (tool_id, policy_val) in map {
        let Some(policy_str) = policy_val.as_str() else {
            return Err(format!("policy for {tool_id} must be a string"));
        };
        let policy = ToolPolicy::from_str(policy_str).ok_or_else(|| {
            format!("invalid policy for {tool_id}: {policy_str}")
        })?;
        out.insert(tool_id.clone(), policy);
    }
    Ok(out)
}

/// Apply per-tool overrides; later entries win for duplicate tool ids.
pub fn apply_per_tool_overrides(ctx: &mut PolicyContext, overrides: HashMap<String, ToolPolicy>) {
    ctx.per_tool.extend(overrides);
}

fn item_object(item: &Value) -> Option<&Map<String, Value>> {
    item.as_object()
}

fn str_field(obj: &Map<String, Value>, key: &str) -> String {
    obj.get(key)
        .map(value_to_string)
        .unwrap_or_default()
}

fn copy_dict_list(items: &Value) -> Vec<Value> {
    let Some(arr) = items.as_array() else {
        return Vec::new();
    };
    arr.iter()
        .filter(|x| x.is_object())
        .map(|x| x.clone())
        .collect()
}

/// Python `not schema.get("properties")` (missing, null, or empty object).
fn properties_field_empty(schema: &Map<String, Value>) -> bool {
    match schema.get("properties") {
        None | Some(Value::Null) => true,
        Some(Value::Object(o)) => o.is_empty(),
        _ => false,
    }
}

pub fn is_non_system_tool_id(tool_id: &str) -> bool {
    tool_id.starts_with("mcp__")
}

pub fn is_system_tool_id(tool_id: &str) -> bool {
    !is_non_system_tool_id(tool_id)
}

pub fn chunk_tool_id(item: &Value) -> String {
    let Some(obj) = item_object(item) else {
        return String::new();
    };
    if let Some(id) = obj.get("id") {
        return value_to_string(id);
    }
    if let Some(name) = obj.get("name") {
        return value_to_string(name);
    }
    String::new()
}

pub fn effective_policy(ctx: &PolicyContext, tool_id: &str) -> ToolPolicy {
    if let Some(p) = ctx.per_tool.get(tool_id) {
        return *p;
    }
    if is_system_tool_id(tool_id) {
        ctx.system_policy
    } else {
        ctx.mcp_policy
    }
}

pub fn tool_pass_through(ctx: &PolicyContext, tool_id: &str) -> bool {
    effective_policy(ctx, tool_id) == ToolPolicy::AlwaysInclude
}

pub fn root_tool_id_from_chunk(item: &Value) -> String {
    let Some(obj) = item_object(item) else {
        return chunk_tool_id(item);
    };
    let file_path = str_field(obj, "file_path");
    if let Some(root_key) = get_root_tool_key(&file_path) {
        return tool_id_from_decomposed_rel(&root_key);
    }
    chunk_tool_id(item)
}

pub fn request_pass_through(ctx: &PolicyContext, tools: &[Value]) -> bool {
    let named: Vec<_> = tools
        .iter()
        .filter_map(item_object)
        .filter(|obj| !str_field(obj, "name").is_empty())
        .collect();
    if named.is_empty() {
        return true;
    }
    named
        .iter()
        .all(|obj| tool_pass_through(ctx, &str_field(obj, "name")))
}

pub fn is_non_system_chunk(item: &Value) -> bool {
    is_non_system_tool_id(&chunk_tool_id(item))
}

pub fn is_system_chunk(item: &Value) -> bool {
    is_system_tool_id(&chunk_tool_id(item))
}

pub fn is_decomposed_tool_root_chunk(item: &Value) -> bool {
    let Some(obj) = item_object(item) else {
        return false;
    };
    let file_path = str_field(obj, "file_path");
    if file_path.is_empty() {
        return false;
    }
    let Some(root_key) = get_root_tool_key(&file_path) else {
        return false;
    };
    let Some(decomposed_key) = to_decomposed_key(&file_path) else {
        return false;
    };
    root_key == decomposed_key
}

pub fn is_decomposed_optional_property_chunk(item: &Value) -> bool {
    let Some(obj) = item_object(item) else {
        return false;
    };
    let file_path = str_field(obj, "file_path");
    if file_path.is_empty() {
        return false;
    }
    let Some(decomposed_key) = to_decomposed_key(&file_path) else {
        return false;
    };
    let Some(root_key) = get_root_tool_key(&file_path) else {
        return false;
    };
    root_key != decomposed_key
}

pub fn is_system_root_chunk(item: &Value) -> bool {
    is_system_chunk(item) && is_decomposed_tool_root_chunk(item)
}

pub fn is_mcp_root_chunk(item: &Value) -> bool {
    is_non_system_chunk(item) && is_decomposed_tool_root_chunk(item)
}

pub fn is_system_optional_chunk(item: &Value) -> bool {
    is_system_chunk(item) && is_decomposed_optional_property_chunk(item)
}

pub fn is_mcp_optional_chunk(item: &Value) -> bool {
    is_non_system_chunk(item) && is_decomposed_optional_property_chunk(item)
}

pub fn needs_partition(ctx: &PolicyContext) -> bool {
    ctx.system_policy == ToolPolicy::PruneOptional || ctx.mcp_policy == ToolPolicy::PruneOptional
}

pub fn uses_pruned_recompose(policy: ToolPolicy) -> bool {
    matches!(
        policy,
        ToolPolicy::PruneOptional | ToolPolicy::PruneAll
    )
}

pub fn needs_pruned_recompose(ctx: &PolicyContext) -> bool {
    uses_pruned_recompose(ctx.system_policy) || uses_pruned_recompose(ctx.mcp_policy)
}

pub fn chunk_policy(item: &Value, ctx: &PolicyContext) -> Option<ToolPolicy> {
    if is_system_chunk(item) {
        Some(ctx.system_policy)
    } else if is_non_system_chunk(item) {
        Some(ctx.mcp_policy)
    } else {
        None
    }
}

pub fn system_tools_pass_through(ctx: &PolicyContext) -> bool {
    ctx.system_policy == ToolPolicy::AlwaysInclude
}

pub fn mcp_tools_pass_through(ctx: &PolicyContext) -> bool {
    ctx.mcp_policy == ToolPolicy::AlwaysInclude
}

pub fn full_pass_through(ctx: &PolicyContext) -> bool {
    ctx.system_policy == ToolPolicy::AlwaysInclude && ctx.mcp_policy == ToolPolicy::AlwaysInclude
}

pub fn collect_enum_values_from_chunks(chunks: &[Value]) -> HashSet<String> {
    let mut values = HashSet::new();
    for item in chunks {
        if let Some(content) = item_object(item).and_then(|o| o.get("content")) {
            for val in collect_enums(content) {
                values.insert(value_to_string(&val));
            }
        }
    }
    values
}

fn enum_md_matches_values(md_item: &Value, enum_values: &HashSet<String>) -> bool {
    if enum_values.is_empty() {
        return false;
    }
    let Some(content) = item_object(md_item).and_then(|o| o.get("content")) else {
        return false;
    };
    enum_values.contains(&value_to_string(content))
}

fn should_pin_json_chunk(ctx: &PolicyContext, item: &Value) -> bool {
    if !is_decomposed_tool_root_chunk(item) {
        return false;
    }
    effective_policy(ctx, &root_tool_id_from_chunk(item)) == ToolPolicy::PruneOptional
}

pub fn catalog_needs_partition(data: &Value, ctx: &PolicyContext) -> bool {
    if needs_partition(ctx) {
        return true;
    }
    let Some(json_items) = data.get("json").and_then(Value::as_array) else {
        return false;
    };
    let mut seen = HashSet::new();
    for item in json_items {
        if !item.is_object() {
            continue;
        }
        let tool_id = root_tool_id_from_chunk(item);
        if !seen.insert(tool_id.clone()) {
            continue;
        }
        if effective_policy(ctx, &tool_id) == ToolPolicy::PruneOptional {
            return true;
        }
    }
    false
}

pub fn catalog_needs_pruned_recompose(data: &Value, ctx: &PolicyContext) -> bool {
    if needs_pruned_recompose(ctx) {
        return true;
    }
    let Some(json_items) = data.get("json").and_then(Value::as_array) else {
        return false;
    };
    let mut seen = HashSet::new();
    for item in json_items {
        if !item.is_object() {
            continue;
        }
        let tool_id = root_tool_id_from_chunk(item);
        if !seen.insert(tool_id.clone()) {
            continue;
        }
        if uses_pruned_recompose(effective_policy(ctx, &tool_id)) {
            return true;
        }
    }
    false
}

fn partition_json_items(
    ctx: &PolicyContext,
    json_list: &[Value],
) -> (
    Vec<Value>,
    Vec<Value>,
    HashSet<String>,
    HashSet<String>,
    HashMap<String, HashSet<String>>,
) {
    let mut pinned_json = Vec::new();
    let mut processable_json = Vec::new();
    let mut system_required_enums = HashSet::new();
    let mut mcp_required_enums = HashSet::new();
    let mut required_enums_by_tool: HashMap<String, HashSet<String>> = HashMap::new();

    for item in json_list {
        if !item.is_object() {
            continue;
        }
        if should_pin_json_chunk(ctx, item) {
            let copy_item = item.clone();
            pinned_json.push(copy_item.clone());
            let tool_id = root_tool_id_from_chunk(item);
            let enum_vals = collect_enum_values_from_chunks(std::slice::from_ref(&copy_item));
            required_enums_by_tool
                .entry(tool_id.clone())
                .or_default()
                .extend(enum_vals.iter().cloned());
            if is_system_chunk(item) {
                system_required_enums.extend(enum_vals.iter().cloned());
            } else if is_non_system_chunk(item) {
                mcp_required_enums.extend(enum_vals.iter().cloned());
            }
        } else {
            processable_json.push(item.clone());
        }
    }

    (
        pinned_json,
        processable_json,
        system_required_enums,
        mcp_required_enums,
        required_enums_by_tool,
    )
}

fn partition_md_items(
    md_list: &[Value],
    pinned_enum_values: &HashSet<String>,
) -> (Vec<Value>, Vec<Value>) {
    let mut processable_md = Vec::new();
    let mut pinned_md = Vec::new();

    for md_item in md_list {
        if !md_item.is_object() {
            continue;
        }
        let copy_item = md_item.clone();
        if enum_md_matches_values(&copy_item, pinned_enum_values) {
            pinned_md.push(copy_item);
        } else {
            processable_md.push(copy_item);
        }
    }

    (processable_md, pinned_md)
}

pub fn partition_catalog(data: &Value, ctx: &PolicyContext) -> (Value, Value) {
    if !catalog_needs_partition(data, ctx) {
        return (data.clone(), json!({}));
    }

    let json_list = data.get("json").and_then(Value::as_array);
    let md_list = data.get("md").and_then(Value::as_array);
    let json_list = json_list.map(|a| a.as_slice()).unwrap_or(&[]);
    let md_list = md_list.map(|a| a.as_slice()).unwrap_or(&[]);

    const METADATA_KEYS: &[&str] = &[
        "json",
        "md",
        "system_required_enum_values",
        "mcp_required_enum_values",
        "required_enum_values_by_tool",
    ];

    let mut processable = Map::new();
    if let Some(obj) = data.as_object() {
        for (k, v) in obj {
            if !METADATA_KEYS.contains(&k.as_str()) {
                processable.insert(k.clone(), v.clone());
            }
        }
    }

    let mut pinned = Map::new();
    pinned.insert("json".into(), Value::Array(Vec::new()));
    pinned.insert("md".into(), Value::Array(Vec::new()));
    pinned.insert(
        "system_required_enum_values".into(),
        Value::Array(Vec::new()),
    );
    pinned.insert("mcp_required_enum_values".into(), Value::Array(Vec::new()));
    pinned.insert(
        "required_enum_values_by_tool".into(),
        Value::Object(Map::new()),
    );

    let (
        pinned_json,
        processable_json,
        system_required_enums,
        mcp_required_enums,
        required_enums_by_tool,
    ) = partition_json_items(ctx, json_list);

    let mut pinned_enum_values = HashSet::new();
    for vals in required_enums_by_tool.values() {
        pinned_enum_values.extend(vals.iter().cloned());
    }

    let (processable_md, pinned_md) = partition_md_items(md_list, &pinned_enum_values);

    processable.insert("json".into(), Value::Array(processable_json));
    processable.insert("md".into(), Value::Array(processable_md));
    pinned.insert("json".into(), Value::Array(pinned_json));
    pinned.insert("md".into(), Value::Array(pinned_md));

    let mut system_sorted: Vec<_> = system_required_enums.into_iter().collect();
    system_sorted.sort();
    let mut mcp_sorted: Vec<_> = mcp_required_enums.into_iter().collect();
    mcp_sorted.sort();
    pinned.insert(
        "system_required_enum_values".into(),
        Value::Array(system_sorted.into_iter().map(Value::String).collect()),
    );
    pinned.insert(
        "mcp_required_enum_values".into(),
        Value::Array(mcp_sorted.into_iter().map(Value::String).collect()),
    );

    let mut by_tool = Map::new();
    for (tool_id, mut vals) in required_enums_by_tool {
        let mut sorted: Vec<_> = vals.drain().collect();
        sorted.sort();
        by_tool.insert(
            tool_id,
            Value::Array(sorted.into_iter().map(Value::String).collect()),
        );
    }
    pinned.insert("required_enum_values_by_tool".into(), Value::Object(by_tool));

    (Value::Object(processable), Value::Object(pinned))
}

pub fn merge_catalog(processed: &Value, pinned: &Value) -> Value {
    let mut merged = processed.clone();
    let merged_obj = merged.as_object_mut().expect("catalog must be object");

    if let Some(pinned_json) = pinned.get("json").and_then(Value::as_array) {
        let arr = merged_obj
            .entry("json".to_string())
            .or_insert_with(|| Value::Array(Vec::new()));
        if let Some(merged_json) = arr.as_array_mut() {
            merged_json.extend(pinned_json.iter().cloned());
        }
    }
    if let Some(pinned_md) = pinned.get("md").and_then(Value::as_array) {
        let arr = merged_obj
            .entry("md".to_string())
            .or_insert_with(|| Value::Array(Vec::new()));
        if let Some(merged_md) = arr.as_array_mut() {
            merged_md.extend(pinned_md.iter().cloned());
        }
    }
    if pinned.get("system_required_enum_values").is_some() {
        if let Some(v) = pinned.get("system_required_enum_values") {
            merged_obj.insert("system_required_enum_values".into(), v.clone());
        }
    }
    if pinned.get("mcp_required_enum_values").is_some() {
        if let Some(v) = pinned.get("mcp_required_enum_values") {
            merged_obj.insert("mcp_required_enum_values".into(), v.clone());
        }
    }
    if pinned.get("required_enum_values_by_tool").is_some() {
        if let Some(v) = pinned.get("required_enum_values_by_tool") {
            merged_obj.insert("required_enum_values_by_tool".into(), v.clone());
        }
    }
    merged
}

pub fn stash_system_tools(tools: &[Value]) -> Vec<Value> {
    tools
        .iter()
        .filter(|t| {
            item_object(t)
                .map(|o| is_system_tool_id(&str_field(o, "name")))
                .unwrap_or(false)
        })
        .cloned()
        .collect()
}

pub fn restore_system_tools(stash: &[Value]) -> Vec<Value> {
    stash.to_vec()
}

pub fn stash_mcp_tools(tools: &[Value]) -> Vec<Value> {
    tools
        .iter()
        .filter(|t| {
            item_object(t)
                .map(|o| is_non_system_tool_id(&str_field(o, "name")))
                .unwrap_or(false)
        })
        .cloned()
        .collect()
}

pub fn restore_mcp_tools(stash: &[Value]) -> Vec<Value> {
    stash.to_vec()
}

pub fn merge_tools_preserving_order(
    original: &[Value],
    pruned_by_name: &HashMap<String, Value>,
    stashed_by_name: &HashMap<String, Value>,
) -> Vec<Value> {
    let mut result = Vec::new();
    for tool in original {
        let Some(obj) = item_object(tool) else {
            continue;
        };
        let name = str_field(obj, "name");
        if name.is_empty() {
            continue;
        }
        if let Some(t) = stashed_by_name.get(&name) {
            result.push(t.clone());
        } else if let Some(t) = pruned_by_name.get(&name) {
            result.push(t.clone());
        }
    }
    result
}

pub fn anthropic_tool_is_system(tool: &Value) -> bool {
    item_object(tool)
        .map(|o| is_system_tool_id(&str_field(o, "name")))
        .unwrap_or(false)
}

pub fn anthropic_tool_is_mcp(tool: &Value) -> bool {
    item_object(tool)
        .map(|o| is_non_system_tool_id(&str_field(o, "name")))
        .unwrap_or(false)
}

pub fn split_anthropic_tools(tools: &[Value]) -> (Vec<Value>, Vec<Value>) {
    let mut non_system = Vec::new();
    let mut system = Vec::new();
    for tool in tools {
        if anthropic_tool_is_system(tool) {
            system.push(tool.clone());
        } else {
            non_system.push(tool.clone());
        }
    }
    (non_system, system)
}

pub fn entries_for_policy(ctx: &PolicyContext, all_entries: &[Value]) -> Vec<Value> {
    let mut result = Vec::new();
    for entry in all_entries {
        let tool_id = item_object(entry)
            .map(|o| str_field(o, "id"))
            .unwrap_or_default();
        if !tool_id.is_empty() && tool_pass_through(ctx, &tool_id) {
            continue;
        }
        result.push(entry.clone());
    }
    result
}

pub fn tools_for_catalog(ctx: &PolicyContext, tools: &[Value]) -> Vec<Value> {
    let mut result = Vec::new();
    for tool in tools {
        let name = item_object(tool)
            .map(|o| str_field(o, "name"))
            .unwrap_or_default();
        if !name.is_empty() && tool_pass_through(ctx, &name) {
            continue;
        }
        result.push(tool.clone());
    }
    result
}

pub fn system_required_enum_values(data: &Value) -> HashSet<String> {
    data.get("system_required_enum_values")
        .and_then(Value::as_array)
        .map(|arr| arr.iter().map(value_to_string).collect())
        .unwrap_or_default()
}

pub fn mcp_required_enum_values(data: &Value) -> HashSet<String> {
    data.get("mcp_required_enum_values")
        .and_then(Value::as_array)
        .map(|arr| arr.iter().map(value_to_string).collect())
        .unwrap_or_default()
}

pub fn required_enum_values_by_tool(data: &Value) -> HashMap<String, HashSet<String>> {
    let Some(raw) = data.get("required_enum_values_by_tool").and_then(Value::as_object) else {
        return HashMap::new();
    };
    raw.iter()
        .filter_map(|(tool_id, values)| {
            let set: HashSet<String> = values
                .as_array()?
                .iter()
                .map(value_to_string)
                .collect();
            Some((tool_id.clone(), set))
        })
        .collect()
}

pub fn optional_leaf_survived_rerank(
    ctx: &PolicyContext,
    item: &Value,
    rerank_score: f64,
    llm_selected_paths: Option<&HashSet<String>>,
) -> bool {
    if !is_decomposed_optional_property_chunk(item) {
        return false;
    }
    let file_path = item_object(item)
        .map(|o| str_field(o, "file_path"))
        .unwrap_or_default();
    if let Some(paths) = llm_selected_paths {
        if paths.contains(&file_path) {
            return true;
        }
    }
    let policy = effective_policy(ctx, &root_tool_id_from_chunk(item));
    match policy {
        ToolPolicy::PruneAll => true,
        ToolPolicy::PruneOptional => item_object(item)
            .and_then(|o| o.get("score"))
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            >= rerank_score,
        ToolPolicy::AlwaysInclude => false,
    }
}

pub fn filter_recompose_json_entries(
    ctx: &PolicyContext,
    json_list: &[Value],
    rerank_score: f64,
    llm_selected_paths: Option<&HashSet<String>>,
) -> Vec<Value> {
    let mut filtered = Vec::new();
    for item in json_list {
        if is_decomposed_tool_root_chunk(item) {
            filtered.push(item.clone());
        } else if optional_leaf_survived_rerank(
            ctx,
            item,
            rerank_score,
            llm_selected_paths,
        ) {
            filtered.push(item.clone());
        }
    }
    filtered
}

pub fn is_direct_root_optional_property_chunk(item: &Value) -> bool {
    if !is_decomposed_optional_property_chunk(item) {
        return false;
    }
    let file_path = item_object(item)
        .map(|o| str_field(o, "file_path"))
        .unwrap_or_default();
    let Some(key) = to_decomposed_key(&file_path) else {
        return false;
    };
    let root = decomposed_root();
    let Ok(rel) = Path::new(&key).strip_prefix(&root) else {
        return false;
    };
    let parts: Vec<_> = rel.components().collect();
    parts.len() == 2
        && parts[1]
            .as_os_str()
            .to_string_lossy()
            .ends_with(&json_ext())
}

fn chunk_input_schema(item: &Value) -> Map<String, Value> {
    let Some(content) = item_object(item)
        .and_then(|o| o.get("content"))
        .and_then(Value::as_object)
    else {
        return Map::new();
    };
    if let Some(schema) = content
        .get("inputSchema")
        .or_else(|| content.get("input_schema"))
        .and_then(Value::as_object)
    {
        return schema.clone();
    }
    Map::new()
}

pub fn root_chunk_properties_empty(item: &Value) -> bool {
    if !is_decomposed_tool_root_chunk(item) {
        return false;
    }
    properties_field_empty(&chunk_input_schema(item))
}

pub fn tool_id_has_empty_decomposed_root(catalog_index: &CatalogIndex, tool_id: &str) -> bool {
    let rel = format!("{}{tool_id}{}", decomposed_prefix(), json_ext());
    let Some(raw) = catalog_index.files.get(&rel) else {
        return false;
    };
    let parsed: Value = serde_json::from_str(raw).unwrap_or(Value::Null);
    let schema = parsed
        .get("inputSchema")
        .or_else(|| parsed.get("input_schema"))
        .and_then(Value::as_object);
    let Some(schema) = schema else {
        return true;
    };
    properties_field_empty(schema)
}

fn original_tool_input_schema(catalog_index: &CatalogIndex, tool_id: &str) -> Map<String, Value> {
    let full_rel = format!("schemas/full/{tool_id}{}", json_ext());
    if let Some(raw) = catalog_index.files.get(&full_rel) {
        if let Ok(parsed) = serde_json::from_str::<Value>(raw) {
            if let Some(schema) = parsed
                .get("inputSchema")
                .or_else(|| parsed.get("input_schema"))
                .and_then(Value::as_object)
            {
                return schema.clone();
            }
        }
    }
    for entry in &catalog_index.tools {
        if item_object(entry).map(|o| str_field(o, "id")).as_deref() != Some(tool_id) {
            continue;
        }
        if let Some(full_schema) = entry.get("full_schema").and_then(Value::as_object) {
            if let Some(schema) = full_schema
                .get("inputSchema")
                .or_else(|| full_schema.get("input_schema"))
                .and_then(Value::as_object)
            {
                return schema.clone();
            }
        }
    }
    Map::new()
}

pub fn tool_id_had_empty_original_root_properties(
    catalog_index: &CatalogIndex,
    tool_id: &str,
) -> bool {
    properties_field_empty(&original_tool_input_schema(catalog_index, tool_id))
}

pub fn needs_empty_optional_mitigation(catalog_index: &CatalogIndex, tool_id: &str) -> bool {
    tool_id_has_empty_decomposed_root(catalog_index, tool_id)
        && !tool_id_had_empty_original_root_properties(catalog_index, tool_id)
}

pub fn optional_chunks_for_tool(items: &[Value], tool_id: &str) -> Vec<Value> {
    items
        .iter()
        .filter(|item| {
            item.is_object()
                && is_decomposed_optional_property_chunk(item)
                && root_tool_id_from_chunk(item) == tool_id
        })
        .cloned()
        .collect()
}

pub fn direct_root_optional_chunks_for_tool(items: &[Value], tool_id: &str) -> Vec<Value> {
    optional_chunks_for_tool(items, tool_id)
        .into_iter()
        .filter(|item| is_direct_root_optional_property_chunk(item))
        .collect()
}

fn scored_json_entries(post_rerank_scored: Option<&Value>) -> Vec<Value> {
    let Some(data) = post_rerank_scored.and_then(Value::as_object) else {
        return Vec::new();
    };
    copy_dict_list(data.get("json").unwrap_or(&Value::Null))
}

fn should_mitigate_empty_root(
    ctx: &PolicyContext,
    tool_id: &str,
    root_item: &Value,
    entries: &[Value],
    catalog_index: &CatalogIndex,
) -> bool {
    if !uses_pruned_recompose(effective_policy(ctx, tool_id)) {
        return false;
    }
    if !needs_empty_optional_mitigation(catalog_index, tool_id) {
        return false;
    }
    if !root_chunk_properties_empty(root_item) {
        return false;
    }
    optional_chunks_for_tool(entries, tool_id).is_empty()
}

fn append_rerank_fallback_chunks(
    tool_id: &str,
    result: &mut Vec<Value>,
    seen_paths: &mut HashSet<String>,
    scored_json: &[Value],
) {
    let mut candidates = optional_chunks_for_tool(scored_json, tool_id);
    candidates.sort_by(|a, b| {
        let sa = item_object(a)
            .and_then(|o| o.get("score"))
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        let sb = item_object(b)
            .and_then(|o| o.get("score"))
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
    });
    for chunk in candidates
        .into_iter()
        .take(runtime_config::empty_optional_fallback_k())
    {
        let file_path = item_object(&chunk)
            .and_then(|o| o.get("file_path"))
            .map(value_to_string)
            .unwrap_or_default();
        if file_path.is_empty() || !seen_paths.insert(file_path) {
            continue;
        }
        result.push(chunk);
    }
}

fn tool_roots_from_entries(entries: &[Value]) -> HashMap<String, Value> {
    let mut roots_by_tool = HashMap::new();
    for item in entries {
        if item.is_object() && is_decomposed_tool_root_chunk(item) {
            roots_by_tool.insert(root_tool_id_from_chunk(item), item.clone());
        }
    }
    roots_by_tool
}

fn drop_tools_from_entries(entries: &[Value], tools_to_drop: &HashSet<String>) -> Vec<Value> {
    if tools_to_drop.is_empty() {
        return entries.to_vec();
    }
    entries
        .iter()
        .filter(|item| {
            item.is_object() && !tools_to_drop.contains(&root_tool_id_from_chunk(item))
        })
        .cloned()
        .collect()
}

pub fn mitigate_empty_optional_properties(
    ctx: &PolicyContext,
    entries: &[Value],
    catalog_index: &CatalogIndex,
    post_rerank_scored: Option<&Value>,
    pipeline: &[String],
) -> Vec<Value> {
    if pipeline.is_empty() || entries.is_empty() {
        return entries.to_vec();
    }
    let last_stage = pipeline.last().map(String::as_str).unwrap_or("");
    if !matches!(last_stage, "rerank" | "llm" | "bm25") {
        return entries.to_vec();
    }

    let roots_by_tool = tool_roots_from_entries(entries);
    if roots_by_tool.is_empty() {
        return entries.to_vec();
    }

    let scored_json = scored_json_entries(post_rerank_scored);
    let mut result: Vec<Value> = entries.to_vec();
    let mut seen_paths: HashSet<String> = result
        .iter()
        .filter_map(|item| {
            item_object(item)
                .and_then(|o| o.get("file_path"))
                .map(value_to_string)
        })
        .collect();
    let mut tools_to_drop = HashSet::new();

    for (tool_id, root_item) in &roots_by_tool {
        if !should_mitigate_empty_root(ctx, tool_id, root_item, &result, catalog_index) {
            continue;
        }
        if last_stage == "llm" {
            tools_to_drop.insert(tool_id.clone());
            continue;
        }
        if matches!(last_stage, "rerank" | "bm25") && !scored_json.is_empty() {
            append_rerank_fallback_chunks(tool_id, &mut result, &mut seen_paths, &scored_json);
        }
    }

    drop_tools_from_entries(&result, &tools_to_drop)
}

pub fn drop_recomposed_tools_with_empty_properties(
    ctx: &PolicyContext,
    tools: &[Value],
    catalog_index: &CatalogIndex,
) -> Vec<Value> {
    let mut kept = Vec::new();
    for tool in tools {
        let name = item_object(tool)
            .map(|o| str_field(o, "name"))
            .unwrap_or_default();
        let schema = item_object(tool)
            .and_then(|o| o.get("inputSchema").or_else(|| o.get("input_schema")))
            .and_then(Value::as_object);
        let has_props = schema.is_some_and(|s| !properties_field_empty(s));
        if has_props {
            kept.push(tool.clone());
            continue;
        }
        if !name.is_empty()
            && uses_pruned_recompose(effective_policy(ctx, &name))
            && needs_empty_optional_mitigation(catalog_index, &name)
        {
            continue;
        }
        kept.push(tool.clone());
    }
    kept
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_policy_roundtrip() {
        for s in [ALWAYS_INCLUDE, PRUNE_OPTIONAL, PRUNE_ALL] {
            let p = ToolPolicy::from_str(s).unwrap();
            assert_eq!(p.as_str(), s);
        }
    }

    #[test]
    fn mcp_tool_id_detection() {
        assert!(is_non_system_tool_id("mcp__foo"));
        assert!(!is_system_tool_id("mcp__foo"));
    }

    #[test]
    fn parse_tool_policy_pair_valid() {
        let (tool, policy) = parse_tool_policy_pair("Agent=always_include").unwrap();
        assert_eq!(tool, "Agent");
        assert_eq!(policy, ToolPolicy::AlwaysInclude);
    }

    #[test]
    fn per_tool_policies_from_value_parses_object() {
        let val = json!({
            "Agent": "prune_optional",
            "mcp__fff__grep": "always_include"
        });
        let map = per_tool_policies_from_value(&val).unwrap();
        assert_eq!(map.get("Agent"), Some(&ToolPolicy::PruneOptional));
        assert_eq!(map.get("mcp__fff__grep"), Some(&ToolPolicy::AlwaysInclude));
    }

    #[test]
    fn policy_context_prefers_pruning_policy_over_defaults() {
        let config = json!({
            "pruning": {
                "policy": {
                    "system_tool": "always_include",
                    "mcp_tool": "prune_optional"
                }
            },
            "defaults": {
                "system_tool_policy": "prune_all",
                "mcp_tool_policy": "prune_all"
            }
        });
        let ctx = policy_context_from_values(&config);
        assert_eq!(ctx.system_policy, ToolPolicy::AlwaysInclude);
        assert_eq!(ctx.mcp_policy, ToolPolicy::PruneOptional);
    }

    #[test]
    fn policy_context_falls_back_to_defaults_policy() {
        let config = json!({
            "defaults": {
                "system_tool_policy": "prune_all",
                "mcp_tool_policy": "always_include"
            }
        });
        let ctx = policy_context_from_values(&config);
        assert_eq!(ctx.system_policy, ToolPolicy::PruneAll);
        assert_eq!(ctx.mcp_policy, ToolPolicy::AlwaysInclude);
    }
}
