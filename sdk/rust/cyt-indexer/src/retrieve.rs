//! Decomposed catalog retrieval: merge tool schemas, score filtering, and enum pruning.

use crate::build::{CatalogIndex, catalog_index_from_value};
use crate::paths::{
    self, decomposed_prefix, get_root_tool_key, json_ext, md_ext, tool_id_from_decomposed_rel,
};
use crate::policies::{
    PolicyContext, ToolPolicy, append_description_reinstate_entries, effective_policy,
    mcp_required_enum_values, needs_description_reinstate, required_enum_values_by_tool,
    system_required_enum_values,
};
use crate::runtime_config;
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// In-memory map of decomposed JSON schema files keyed by catalog-relative path.
#[derive(Debug, Clone, Default)]
pub struct DecomposedCatalog {
    /// Parsed JSON object per decomposed file path.
    pub(crate) json_files: HashMap<String, Value>,
}

impl DecomposedCatalog {
    /// Wrap a pre-built path-to-JSON map.
    #[must_use]
    pub const fn from_json_files(json_files: HashMap<String, Value>) -> Self {
        Self { json_files }
    }

    /// Borrow the underlying path-to-JSON map.
    #[must_use]
    pub const fn json_files(&self) -> &HashMap<String, Value> {
        &self.json_files
    }

    /// Load decomposed JSON files from a [`CatalogIndex`] file table.
    #[must_use]
    pub fn from_catalog_index(index: &CatalogIndex) -> Self {
        let mut json_files = HashMap::new();
        for (rel_path, content) in &index.files {
            if rel_path.starts_with(&decomposed_prefix())
                && rel_path.ends_with(&json_ext())
                && let Ok(parsed) = serde_json::from_str::<Value>(content)
                && parsed.is_object()
            {
                json_files.insert(rel_path.clone(), parsed);
            }
        }
        Self { json_files }
    }

    /// Load decomposed JSON from a survivor/catalog dict (`json` array entries).
    #[must_use]
    pub fn from_catalog_dict(data: &Value) -> Self {
        let mut json_files = HashMap::new();
        if let Some(entries) = data.get("json").and_then(|v| v.as_array()) {
            for entry in entries {
                let Some(obj) = entry.as_object() else {
                    continue;
                };
                let Some(file_path) = obj.get("file_path").and_then(|v| v.as_str()) else {
                    continue;
                };
                let Some(content) = obj.get("content") else {
                    continue;
                };
                if !content.is_object() {
                    continue;
                }
                if let Some(key) = paths::to_decomposed_key(file_path) {
                    json_files.insert(key, content.clone());
                }
            }
        }
        Self { json_files }
    }

    /// Overlay another catalog's JSON files (later keys win).
    pub fn merge_json_files(&mut self, other: &Self) {
        self.json_files.extend(other.json_files.clone());
    }

    /// Resolve a survivor or absolute path to a stored decomposed key, if present.
    #[must_use]
    pub fn resolve_key(&self, file_path: &str) -> Option<String> {
        let mut candidates = Vec::new();
        if let Some(normalized) = paths::to_decomposed_key(file_path) {
            candidates.push(normalized);
        }
        candidates.push(file_path.to_string());
        candidates
            .into_iter()
            .find(|candidate| self.has_json(candidate))
    }

    /// Whether a decomposed JSON file exists under `key`.
    #[must_use]
    pub fn has_json(&self, key: &str) -> bool {
        self.json_files.contains_key(key)
    }

    /// Borrow parsed JSON for a decomposed file key.
    #[must_use]
    pub fn get_json(&self, key: &str) -> Option<&Value> {
        self.json_files.get(key)
    }
}

/// Parse a host catalog value into [`DecomposedCatalog`] (index dict or json-files map).
#[must_use]
pub fn decomposed_catalog_from_value(val: &Value) -> DecomposedCatalog {
    if val.get("tools").is_some() && val.get("files").is_some() {
        let idx = catalog_index_from_value(val);
        return DecomposedCatalog::from_catalog_index(&idx);
    }
    if let Some(map) = val.as_object() {
        let mut json_files = HashMap::new();
        for (k, v) in map {
            if v.is_object() {
                json_files.insert(k.clone(), v.clone());
            }
        }
        if !json_files.is_empty() {
            return DecomposedCatalog::from_json_files(json_files);
        }
    }
    DecomposedCatalog::default()
}

/// Recursively merge JSON objects; non-object overrides replace the base value.
#[must_use]
pub fn deep_merge(base: &Value, override_val: &Value) -> Value {
    match (base, override_val) {
        (Value::Object(base_map), Value::Object(override_map)) => {
            let mut result = base_map.clone();
            for (key, val) in override_map {
                if let Some(existing) = result.get(key)
                    && existing.is_object()
                    && val.is_object()
                {
                    result.insert(key.clone(), deep_merge(existing, val));
                    continue;
                }
                result.insert(key.clone(), val.clone());
            }
            Value::Object(result)
        }
        _ => override_val.clone(),
    }
}

/// Walk parent decomposed JSON files and deep-merge them over `leaf_path`.
#[must_use]
pub fn climb_and_merge(leaf_path: &str, catalog: &DecomposedCatalog) -> Value {
    let leaf_key = catalog.resolve_key(leaf_path).unwrap_or_else(|| {
        paths::to_decomposed_key(leaf_path).unwrap_or_else(|| leaf_path.to_string())
    });

    let Some(mut current) = catalog.get_json(&leaf_key).cloned() else {
        return json!({});
    };

    let mut current_path = PathBuf::from(&leaf_key);
    current_path.pop();

    let decomposed_root = paths::decomposed_root();

    loop {
        let parent_dir = current_path.parent().map(std::path::Path::to_path_buf);
        let Some(parent_dir) = parent_dir else {
            break;
        };
        if parent_dir == decomposed_root || !parent_dir.starts_with(&decomposed_root) {
            break;
        }

        let parent_key = format!(
            "{}/{}{}",
            parent_dir.to_string_lossy(),
            current_path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy(),
            json_ext(),
        );
        if let Some(parent) = catalog.get_json(&parent_key) {
            current = deep_merge(parent, &current);
        }
        current_path = parent_dir;
    }
    current
}

/// Collect rerank scores keyed by markdown content or json `file_path`.
#[must_use]
pub fn extract_scores(data: &Value) -> HashMap<String, f64> {
    let mut scores = HashMap::new();
    let Some(obj) = data.as_object() else {
        return scores;
    };
    if let Some(md) = obj.get("md").and_then(|v| v.as_array()) {
        for entry in md {
            if let Some(e) = entry.as_object()
                && let (Some(content), Some(score)) = (
                    e.get("content").and_then(|v| v.as_str()),
                    json_f64(e.get("score")),
                )
            {
                scores.insert(content.to_string(), score);
            }
        }
    }
    if let Some(json_arr) = obj.get("json").and_then(|v| v.as_array()) {
        for entry in json_arr {
            if let Some(e) = entry.as_object()
                && let (Some(fp), Some(score)) = (
                    e.get("file_path").and_then(|v| v.as_str()),
                    json_f64(e.get("score")),
                )
            {
                scores.insert(fp.to_string(), score);
            }
        }
    }
    scores
}

/// Parse a JSON number or numeric string (pruner snapshots often store scores as strings).
fn json_f64(value: Option<&Value>) -> Option<f64> {
    let v = value?;
    if let Some(n) = v.as_f64() {
        return Some(n);
    }
    v.as_str().and_then(|s| s.trim().parse::<f64>().ok())
}

fn extract_from_dict(
    data: &Map<String, Value>,
    apply_decomposed_score_filter: bool,
) -> Vec<String> {
    let mut input_files = Vec::new();
    for (key, value) in data {
        if key == "md" {
            continue;
        }
        if let Some(arr) = value.as_array() {
            for entry in arr {
                if let Some(e) = entry.as_object()
                    && let Some(fp) = e.get("file_path").and_then(|v| v.as_str())
                {
                    if key == "json" && apply_decomposed_score_filter {
                        let score = json_f64(e.get("score")).unwrap_or(0.0);
                        if score <= runtime_config::decomposed_score() {
                            continue;
                        }
                    }
                    input_files.push(fp.to_string());
                }
            }
        } else if let Some(e) = value.as_object()
            && let Some(fp) = e.get("file_path").and_then(|v| v.as_str())
        {
            input_files.push(fp.to_string());
        }
    }
    input_files
}

/// List input `file_path` values from pruner/rerank survivor data.
#[must_use]
pub fn extract_input_files(data: &Value, apply_decomposed_score_filter: bool) -> Vec<String> {
    if let Some(obj) = data.as_object() {
        return extract_from_dict(obj, apply_decomposed_score_filter);
    }
    if let Some(arr) = data.as_array() {
        return arr
            .iter()
            .filter_map(|entry| {
                entry
                    .as_object()
                    .and_then(|e| e.get("file_path"))
                    .and_then(|v| v.as_str())
                    .map(String::from)
            })
            .collect();
    }
    Vec::new()
}

/// Parse survivor data into input file paths and score map.
#[must_use]
pub fn parse_json_input(
    data: &Value,
    apply_decomposed_score_filter: bool,
) -> (Vec<String>, HashMap<String, f64>) {
    (
        extract_input_files(data, apply_decomposed_score_filter),
        extract_scores(data),
    )
}

fn filter_items(items_with_scores: &[(Value, f64)]) -> Vec<Value> {
    let first_3_above = items_with_scores
        .iter()
        .take(3)
        .all(|(_, score)| *score >= runtime_config::enum_score());

    if first_3_above {
        items_with_scores
            .iter()
            .filter(|(_, score)| *score >= runtime_config::enum_score())
            .map(|(item, _)| item.clone())
            .collect()
    } else {
        items_with_scores
            .iter()
            .take(3)
            .map(|(item, _)| item.clone())
            .collect()
    }
}

/// Prune and sort JSON-schema `enum` arrays using rerank scores and preserve sets.
pub fn filter_and_sort_enums<S: std::hash::BuildHasher, P: std::hash::BuildHasher>(
    schema: &mut Value,
    scores: &HashMap<String, f64, S>,
    preserve_values: Option<&HashSet<String, P>>,
) {
    match schema {
        Value::Object(map) => {
            let keys: Vec<String> = map.keys().cloned().collect();
            for key in keys {
                if key == "enum" {
                    if let Some(Value::Array(items)) = map.get("enum").cloned() {
                        let mut preserved = Vec::new();
                        let mut prunable = Vec::new();
                        for item in items {
                            if preserve_values.is_some_and(|pv| pv.contains(&item.to_string())) {
                                preserved.push(item);
                            } else {
                                prunable.push(item);
                            }
                        }
                        let mut items_with_scores: Vec<(Value, f64)> = prunable
                            .into_iter()
                            .map(|item| {
                                let score = scores.get(&item.to_string()).copied().unwrap_or(0.0);
                                (item, score)
                            })
                            .collect();
                        items_with_scores.sort_by(|a, b| {
                            b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal)
                        });
                        preserved.extend(filter_items(&items_with_scores));
                        map.insert("enum".into(), Value::Array(preserved));
                    }
                } else if let Some(val) = map.get(&key).cloned() {
                    let mut inner = val;
                    filter_and_sort_enums(&mut inner, scores, preserve_values);
                    map.insert(key, inner);
                }
            }
        }
        Value::Array(items) => {
            for item in items.iter_mut() {
                filter_and_sort_enums(item, scores, preserve_values);
            }
        }
        _ => {}
    }
}

/// Group decomposed file paths by root tool key; track standalone tool JSON files.
#[must_use]
pub fn group_files(
    input_files: &[String],
    catalog: &DecomposedCatalog,
) -> (HashMap<String, Vec<String>>, HashSet<String>) {
    let mut groups: HashMap<String, Vec<String>> = HashMap::new();
    let mut tool_files = HashSet::new();
    let decomposed_root = paths::decomposed_root();

    for file_path in input_files {
        let Some(key) = catalog.resolve_key(file_path) else {
            eprintln!("Warning: File not found: {file_path}");
            continue;
        };
        let rel = Path::new(&key)
            .strip_prefix(&decomposed_root)
            .unwrap_or_else(|_| Path::new(&key));
        let parts: Vec<_> = rel.components().collect();
        let is_tool = parts.len() == 1
            && parts[0]
                .as_os_str()
                .to_string_lossy()
                .ends_with(&json_ext());

        let Some(root_tool) = paths::get_root_tool_key(&key) else {
            continue;
        };
        if is_tool {
            tool_files.insert(key.clone());
        }
        groups.entry(root_tool).or_default().push(key);
    }
    (groups, tool_files)
}

fn tool_shell_from_root_key(root_tool: &str) -> Value {
    let name = Path::new(root_tool)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy();
    json!({
        "name": name,
        "inputSchema": {"type": "object", "properties": {}},
    })
}

/// Build retrieve ``ProcessGroupsOptions`` from policy context and catalog state.
#[must_use]
pub fn build_process_groups_options(
    ctx: &PolicyContext,
    catalog_dict: &Value,
    store: &DecomposedCatalog,
    preserve_values: Option<Vec<String>>,
) -> ProcessGroupsOptions {
    let mut system_preserve = system_required_enum_values(catalog_dict);
    if let Some(pv) = preserve_values
        && system_preserve.is_empty()
    {
        system_preserve = pv.into_iter().collect();
    }
    let mcp_preserve = mcp_required_enum_values(catalog_dict);
    let required_by_tool = required_enum_values_by_tool(catalog_dict);

    let mut prune_optional_tools = HashSet::new();
    for key in store.json_files().keys() {
        if let Some(root_tool) = get_root_tool_key(key) {
            let tool_name = tool_id_from_decomposed_rel(&root_tool);
            let policy = effective_policy(ctx, &tool_name);
            if matches!(
                policy,
                ToolPolicy::PruneOptional | ToolPolicy::PruneOptionalDescriptions
            ) {
                prune_optional_tools.insert(tool_name);
            }
        }
    }

    ProcessGroupsOptions {
        system_preserve: (!system_preserve.is_empty()).then_some(system_preserve),
        mcp_preserve: (!mcp_preserve.is_empty()).then_some(mcp_preserve),
        required_by_tool,
        prune_optional_tools,
    }
}

/// Enum-preservation and optional-tool pruning settings for [`process_groups`].
#[derive(Debug, Clone, Default)]
pub struct ProcessGroupsOptions {
    /// Enum values that must survive pruning for system tools.
    pub system_preserve: Option<HashSet<String>>,
    /// Enum values that must survive pruning for MCP tools.
    pub mcp_preserve: Option<HashSet<String>>,
    /// Per-tool required enum values from catalog metadata.
    pub required_by_tool: HashMap<String, HashSet<String>>,
    /// Tool names where `effective_policy` == "`prune_optional`" (enum filtering applies).
    pub prune_optional_tools: HashSet<String>,
}

/// Build [`ProcessGroupsOptions`] from optional policy fields (Python/Node FFI).
#[must_use]
pub fn process_groups_options_from_fields<S: std::hash::BuildHasher + Default>(
    system_preserve: Option<Vec<String>>,
    mcp_preserve: Option<Vec<String>>,
    required_by_tool: Option<HashMap<String, Vec<String>, S>>,
    required_enum_values_by_tool: Option<HashMap<String, Vec<String>, S>>,
    prune_optional_tools: Option<Vec<String>>,
) -> ProcessGroupsOptions {
    let required_by_tool = required_by_tool
        .or(required_enum_values_by_tool)
        .unwrap_or_default()
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().collect()))
        .collect();
    ProcessGroupsOptions {
        system_preserve: system_preserve.map(|items| items.into_iter().collect()),
        mcp_preserve: mcp_preserve.map(|items| items.into_iter().collect()),
        required_by_tool,
        prune_optional_tools: prune_optional_tools
            .unwrap_or_default()
            .into_iter()
            .collect(),
    }
}

/// Merge grouped decomposed files into final tool schema values.
#[must_use]
pub fn process_groups<S: std::hash::BuildHasher>(
    groups: &HashMap<String, Vec<String>, S>,
    tool_files: &HashSet<String, S>,
    scores: &HashMap<String, f64, S>,
    catalog: &DecomposedCatalog,
    opts: &ProcessGroupsOptions,
) -> Vec<Value> {
    let mut tools = Vec::new();

    for (root_tool, files) in groups {
        let mut base_tool = catalog
            .get_json(root_tool)
            .cloned()
            .unwrap_or_else(|| tool_shell_from_root_key(root_tool));

        let tool_name_in_schema = base_tool
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        for file_key in files {
            if tool_files.contains(file_key) {
                continue;
            }
            base_tool = deep_merge(&base_tool, &climb_and_merge(file_key, catalog));
        }

        let stem_name = Path::new(root_tool)
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        let tool_name = base_tool
            .get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(if tool_name_in_schema.is_empty() {
                stem_name.as_str()
            } else {
                tool_name_in_schema.as_str()
            })
            .to_string();

        if let Some(obj) = base_tool.as_object().cloned() {
            let mut obj = obj;
            obj.insert("name".into(), Value::String(tool_name.clone()));
            obj.remove("id");
            base_tool = Value::Object(obj);
        }

        if !scores.is_empty() {
            let enum_preserve = if opts.prune_optional_tools.contains(&tool_name) {
                opts.required_by_tool
                    .get(&tool_name)
                    .cloned()
                    .or_else(|| opts.system_preserve.clone())
                    .or_else(|| opts.mcp_preserve.clone())
            } else {
                None
            };
            filter_and_sort_enums(&mut base_tool, scores, enum_preserve.as_ref());
        }
        tools.push(base_tool);
    }
    tools
}

/// Options for [`retrieve_core`] and [`retrieve_tools_from_catalog`].
#[derive(Debug, Clone, Default)]
pub struct RetrieveOptions {
    /// Drop low-score decomposed json entries before grouping.
    pub apply_decomposed_score_filter: bool,
    /// Enum preservation and optional-tool pruning for merged schemas.
    pub process_groups: ProcessGroupsOptions,
}

/// Resolve the full build catalog dict used for reinstatement and enum metadata.
pub fn resolve_build_catalog(catalog: &Value, survivor_data: &Value) -> Value {
    if catalog.get("tools").is_some() && catalog.get("files").is_some() {
        return catalog_index_from_value(catalog).to_catalog_dict();
    }
    if catalog
        .get("json")
        .and_then(Value::as_array)
        .is_some_and(|arr| !arr.is_empty())
    {
        return catalog.clone();
    }
    survivor_data.clone()
}
/// Returns mitigated `{json, md}` data and a survivor overlay whose chunk contents
/// match the reinstated entries (stripped descriptions on pruned optionals).
pub fn apply_description_reinstate_to_data(
    ctx: &PolicyContext,
    data: &Value,
    build_catalog: &Value,
) -> (Value, DecomposedCatalog) {
    let mut retrieve_data = data.clone();
    let mut survivor = DecomposedCatalog::from_catalog_dict(data);
    if !needs_description_reinstate(ctx) {
        return (retrieve_data, survivor);
    }

    let json_entries = data
        .get("json")
        .and_then(Value::as_array)
        .map_or(&[] as &[Value], std::vec::Vec::as_slice);
    let empty_index = CatalogIndex {
        tools: Vec::new(),
        files: HashMap::new(),
    };
    let mitigated =
        append_description_reinstate_entries(ctx, json_entries, build_catalog, &empty_index);
    if let Some(obj) = retrieve_data.as_object_mut() {
        obj.insert("json".into(), Value::Array(mitigated));
    }
    survivor = DecomposedCatalog::from_catalog_dict(&retrieve_data);
    (retrieve_data, survivor)
}

/// High-level retrieve: description reinstatement (when configured) then merge.
pub fn retrieve_tools_from_catalog(
    ctx: &PolicyContext,
    data: &Value,
    build_catalog: &Value,
    store: &mut DecomposedCatalog,
    opts: &RetrieveOptions,
) -> Vec<Value> {
    let (retrieve_data, survivor) = apply_description_reinstate_to_data(ctx, data, build_catalog);
    retrieve_core(&retrieve_data, store, &survivor, opts)
}

/// Merge survivor input into the catalog store and emit reconstructed tool schemas.
pub fn retrieve_core(
    data: &Value,
    store: &mut DecomposedCatalog,
    survivor_overlay: &DecomposedCatalog,
    opts: &RetrieveOptions,
) -> Vec<Value> {
    if !survivor_overlay.json_files.is_empty() {
        store.merge_json_files(survivor_overlay);
    }

    let (input_files, scores) = parse_json_input(data, opts.apply_decomposed_score_filter);
    let (groups, tool_files) = group_files(&input_files, store);
    process_groups(&groups, &tool_files, &scores, store, &opts.process_groups)
}

/// Options for [`removed_chunks`].
#[derive(Debug, Clone, Default)]
pub struct RemovedChunksOptions {
    /// When true, json entries in `surviving` with score <= decomposed threshold are treated
    /// as non-surviving (matches [`RetrieveOptions::apply_decomposed_score_filter`]).
    pub apply_decomposed_score_filter: bool,
}

/// Normalized identity for a catalog chunk entry (`json` or `md` array item).
#[must_use]
pub fn chunk_survivor_key(entry: &Value, section: &str) -> Option<String> {
    let obj = entry.as_object()?;
    if let Some(fp) = obj.get("file_path").and_then(|v| v.as_str()) {
        return paths::to_decomposed_key(fp).or_else(|| Some(fp.to_string()));
    }
    if section == "md"
        && let Some(content) = obj.get("content").and_then(|v| v.as_str())
    {
        return Some(format!("md:content:{content}"));
    }
    None
}

fn survivor_key_sets(
    surviving: &Value,
    apply_decomposed_score_filter: bool,
) -> (HashSet<String>, HashSet<String>) {
    let mut json_keys = HashSet::new();
    let mut md_keys = HashSet::new();
    let Some(obj) = surviving.as_object() else {
        return (json_keys, md_keys);
    };
    if let Some(arr) = obj.get("json").and_then(|v| v.as_array()) {
        for entry in arr {
            let Some(e) = entry.as_object() else {
                continue;
            };
            if apply_decomposed_score_filter {
                let score = json_f64(e.get("score")).unwrap_or(0.0);
                if score <= runtime_config::decomposed_score() {
                    continue;
                }
            }
            if let Some(key) = chunk_survivor_key(entry, "json") {
                json_keys.insert(key);
            }
        }
    }
    if let Some(arr) = obj.get("md").and_then(|v| v.as_array()) {
        for entry in arr {
            if let Some(key) = chunk_survivor_key(entry, "md") {
                md_keys.insert(key);
            }
        }
    }
    (json_keys, md_keys)
}

fn removed_section(full: &Value, section: &str, survivor_keys: &HashSet<String>) -> Vec<Value> {
    let Some(arr) = full.get(section).and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    let mut removed = Vec::new();
    for entry in arr {
        let key = chunk_survivor_key(entry, section);
        if key.as_ref().is_some_and(|k| survivor_keys.contains(k)) {
            continue;
        }
        removed.push(entry.clone());
    }
    removed
}

/// Chunks present in `full_catalog` but not in `surviving` (same `{json, md}` shape as survivors).
#[must_use]
pub fn removed_chunks(
    full_catalog: &Value,
    surviving: &Value,
    opts: &RemovedChunksOptions,
) -> Value {
    let (json_keys, md_keys) = survivor_key_sets(surviving, opts.apply_decomposed_score_filter);
    let json = removed_section(full_catalog, "json", &json_keys);
    let md = removed_section(full_catalog, "md", &md_keys);
    json!({
        "json": json,
        "md": md,
    })
}

/// Walk a directory tree and build a `{json, md}` catalog dict from decomposed files.
///
/// # Errors
///
/// Returns an error when `dir_path` is not a directory, or when a json file cannot be read or parsed.
pub fn load_catalog_from_dir(dir_path: &str) -> Result<Value, String> {
    let root = Path::new(dir_path);
    if !root.is_dir() {
        return Err(format!("Directory not found: {dir_path}"));
    }

    let mut md_entries = Vec::new();
    let mut json_entries = Vec::new();

    for entry in walkdir_light(root)? {
        let path = entry;
        if !path.is_file() {
            continue;
        }
        let path_str = path.to_string_lossy();
        if !paths::is_catalog_decomposed_path(&path_str) {
            continue;
        }
        let suffix = path.extension().and_then(|s| s.to_str()).unwrap_or("");
        let is_skills_md = paths::to_skills_decomposed_key(&path_str).is_some()
            && suffix.eq_ignore_ascii_case(trim_dot(&md_ext()))
            && path.file_name().and_then(|n| n.to_str()) != Some("page_index.json")
            && path.file_name().and_then(|n| n.to_str()) != Some("chunk_index.json");
        if is_skills_md
            || (paths::to_decomposed_key(&path_str).is_some()
                && suffix.eq_ignore_ascii_case(trim_dot(&md_ext())))
        {
            if let Ok(content) = std::fs::read_to_string(&path) {
                md_entries.push(json!({
                    "id": path.file_stem().unwrap_or_default().to_string_lossy(),
                    "file_path": path.to_string_lossy(),
                    "score": 0.0,
                    "start_line": 1,
                    "end_line": 1,
                    "language": "markdown",
                    "content": content,
                }));
            }
        } else if suffix.eq_ignore_ascii_case(trim_dot(&json_ext()))
            && paths::to_decomposed_key(&path_str).is_some()
        {
            let raw_text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
            let content: Value = serde_json::from_str(&raw_text).map_err(|e| e.to_string())?;
            let line_count = raw_text.lines().count();
            let rel_path = path.to_string_lossy();
            let decomposed_key = paths::to_decomposed_key(&rel_path);
            let entry_id = content
                .get("id")
                .cloned()
                .or_else(|| {
                    decomposed_key
                        .as_ref()
                        .map(|k| Value::String(paths::tool_id_from_decomposed_rel(k)))
                })
                .unwrap_or_else(|| {
                    Value::String(
                        path.file_stem()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .into_owned(),
                    )
                });
            json_entries.push(json!({
                "id": entry_id,
                "name": entry_id,
                "file_path": rel_path,
                "score": 0.0,
                "start_line": 1,
                "end_line": line_count,
                "language": "json",
                "content": content,
            }));
        }
    }

    if md_entries.is_empty() && json_entries.is_empty() {
        eprintln!("Warning: No .json or .md files found in {dir_path}");
    }

    Ok(json!({
        "md": md_entries,
        "json": json_entries,
    }))
}

fn trim_dot(ext: &str) -> &str {
    ext.strip_prefix('.').unwrap_or(ext)
}

fn walkdir_light(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut stack = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(dir) = stack.pop() {
        let entries = std::fs::read_dir(&dir).map_err(|e| e.to_string())?;
        for entry in entries {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else {
                files.push(path);
            }
        }
    }
    Ok(files)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn low_rerank_scores_kept_without_score_filter() {
        let data = json!({
            "json": [{
                "file_path": "schemas/decomposed/Agent.json",
                "score": "0.003",
            }]
        });
        let files = extract_input_files(&data, false);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn low_rerank_scores_dropped_with_score_filter() {
        let data = json!({
            "json": [{
                "file_path": "schemas/decomposed/Agent.json",
                "score": "0.003",
            }]
        });
        let files = extract_input_files(&data, true);
        assert!(files.is_empty());
    }

    #[test]
    fn removed_chunks_excludes_survivors_by_decomposed_key() {
        let full = json!({
            "json": [
                {"file_path": "schemas/decomposed/Agent.json", "content": {"name": "Agent"}},
                {"file_path": "schemas/decomposed/Agent/extra.json", "content": {}},
            ],
            "md": [
                {"file_path": "schemas/decomposed/haiku.md", "content": "haiku"},
                {"file_path": "schemas/decomposed/sonnet.md", "content": "sonnet"},
            ],
        });
        let surviving = json!({
            "json": [{"file_path": "src/catalog/schemas/decomposed/Agent.json"}],
            "md": [{"file_path": "src/catalog/schemas/decomposed/haiku.md"}],
        });
        let removed = removed_chunks(&full, &surviving, &RemovedChunksOptions::default());
        let json_removed = removed.get("json").and_then(Value::as_array);
        assert_eq!(json_removed.map(std::vec::Vec::len), Some(1));
        assert_eq!(
            json_removed
                .and_then(|entries| entries.first())
                .and_then(|entry| entry.get("file_path"))
                .and_then(Value::as_str),
            Some("schemas/decomposed/Agent/extra.json")
        );
        let md_removed = removed.get("md").and_then(Value::as_array);
        assert_eq!(md_removed.map(std::vec::Vec::len), Some(1));
        assert_eq!(
            md_removed
                .and_then(|entries| entries.first())
                .and_then(|entry| entry.get("file_path"))
                .and_then(Value::as_str),
            Some("schemas/decomposed/sonnet.md")
        );
    }

    #[test]
    fn removed_chunks_respects_score_filter_on_survivors() {
        let full = json!({
            "json": [
                {"file_path": "schemas/decomposed/Keep.json", "score": 0.9},
                {"file_path": "schemas/decomposed/Drop.json", "score": 0.9},
            ],
        });
        let surviving = json!({
            "json": [
                {"file_path": "schemas/decomposed/Keep.json", "score": 0.9},
                {"file_path": "schemas/decomposed/Drop.json", "score": 0.1},
            ],
        });
        let removed = removed_chunks(
            &full,
            &surviving,
            &RemovedChunksOptions {
                apply_decomposed_score_filter: true,
            },
        );
        let json_removed = removed.get("json").and_then(Value::as_array);
        assert_eq!(json_removed.map(std::vec::Vec::len), Some(1));
        assert_eq!(
            json_removed
                .and_then(|entries| entries.first())
                .and_then(|entry| entry.get("file_path"))
                .and_then(Value::as_str),
            Some("schemas/decomposed/Drop.json")
        );
    }
}
