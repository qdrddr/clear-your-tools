// N-API bindings for policies, documents, and catalog I/O (included from `node.rs`).

/// N-API policy context (defaults live in [`PolicyContext::new`]).
#[napi(js_name = "PolicyContext")]
pub struct PolicyContextNapi {
    pub(crate) inner: PolicyContext,
}

#[napi]
impl PolicyContextNapi {
    #[napi(constructor)]
    #[must_use]
    pub fn new(system_policy: Option<String>, mcp_policy: Option<String>) -> Self {
        Self {
            inner: PolicyContext::with_overrides(
                system_policy.and_then(|s| parse_tool_policy(&s)),
                mcp_policy.and_then(|s| parse_tool_policy(&s)),
                HashMap::new(),
            ),
        }
    }

    #[napi(getter)]
    #[must_use]
    pub fn system_policy(&self) -> String {
        self.inner.system_policy.as_str().to_string()
    }

    #[napi(setter)]
    pub fn set_system_policy(&mut self, value: String) {
        let value = value.into_boxed_str();
        if let Some(p) = parse_tool_policy(value.as_ref()) {
            self.inner.system_policy = p;
        }
    }

    #[napi(getter)]
    #[must_use]
    pub fn mcp_policy(&self) -> String {
        self.inner.mcp_policy.as_str().to_string()
    }

    #[napi(setter)]
    pub fn set_mcp_policy(&mut self, value: String) {
        let value = value.into_boxed_str();
        if let Some(p) = parse_tool_policy(value.as_ref()) {
            self.inner.mcp_policy = p;
        }
    }

    #[napi(getter)]
    #[must_use]
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
            if let Some(p) = parse_tool_policy(&v) {
                per_tool.insert(k, p);
            }
        }
        self.inner.per_tool = per_tool;
    }

    #[napi(getter)]
    #[must_use]
    pub fn tool_kind(&self) -> Option<String> {
        self.inner
            .tool_kind_override
            .map(policies::ToolKind::as_str)
            .map(str::to_string)
    }

    #[napi(setter)]
    pub fn set_tool_kind(&mut self, value: Option<String>) {
        self.inner.tool_kind_override =
            value.and_then(|s| policies::parse_tool_kind(&s));
    }
}

#[must_use]
pub const fn ctx_from_napi(ctx: &PolicyContextNapi) -> &PolicyContext {
    &ctx.inner
}

pub fn ctx_from_js_object(ctx: PolicyContextJs) -> PolicyContext {
    let per_tool = ctx
        .per_tool
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(k, v)| parse_tool_policy(&v).map(|p| (k, p)))
        .collect();
    let mut policy_ctx = PolicyContext::with_overrides(
        ctx.system_policy
            .as_deref()
            .and_then(parse_tool_policy),
        ctx.mcp_policy.as_deref().and_then(parse_tool_policy),
        per_tool,
    );
    policy_ctx.tool_kind_override = ctx
        .tool_kind
        .as_deref()
        .and_then(policies::parse_tool_kind);
    policy_ctx
}

#[napi(object)]
pub struct PolicyContextJs {
    pub system_policy: Option<String>,
    pub mcp_policy: Option<String>,
    pub per_tool: Option<HashMap<String, String>>,
    pub tool_kind: Option<String>,
}

#[must_use]
pub fn ctx_from_any(
    ctx: Option<Either<&PolicyContextNapi, PolicyContextJs>>,
) -> PolicyContext {
    match ctx {
        None => policy_context_from_values(&Value::Object(serde_json::Map::new())),
        Some(Either::A(napi)) => napi.inner.clone(),
        Some(Either::B(js)) => ctx_from_js_object(js),
    }
}

#[napi(js_name = "policyContextFromValues")]
#[must_use]
pub fn policy_context_from_values_napi(config: Value) -> PolicyContextNapi {
    let config = Box::new(config);
    PolicyContextNapi {
        inner: policy_context_from_values(&config),
    }
}

#[napi]
#[must_use]
pub fn effective_policy(ctx: &PolicyContextNapi, tool_id: String) -> String {
    let tool_id = tool_id.into_boxed_str();
    policies::effective_policy(ctx_from_napi(ctx), tool_id.as_ref())
        .as_str()
        .to_string()
}

#[napi]
#[must_use]
pub fn tool_pass_through(ctx: &PolicyContextNapi, tool_id: String) -> bool {
    let tool_id = tool_id.into_boxed_str();
    policies::tool_pass_through(ctx_from_napi(ctx), tool_id.as_ref())
}

#[napi(js_name = "batchToolPassThrough")]
#[must_use]
pub fn batch_tool_pass_through_napi(
    ctx: &PolicyContextNapi,
    tool_ids: Vec<String>,
) -> Vec<bool> {
    let ctx = ctx_from_napi(ctx);
    tool_ids
        .into_iter()
        .map(|id| policies::tool_pass_through(ctx, id.as_str()))
        .collect()
}

/// # Errors
/// Does not fail; returns empty partitions for invalid catalog shapes.
#[napi]
pub fn partition_catalog(
    data: Value,
    ctx: &PolicyContextNapi,
) -> Result<(Value, Value)> {
    let data = Box::new(data);
    let (proc, pinned) = policies::partition_catalog(&data, ctx_from_napi(ctx));
    Ok((proc, pinned))
}

#[napi]
#[must_use]
pub fn merge_catalog(processed: Value, pinned: Value) -> Value {
    let processed = Box::new(processed);
    let pinned = Box::new(pinned);
    policies::merge_catalog(&processed, &pinned)
}

#[napi]
#[must_use]
pub fn catalog_needs_partition(data: Value, ctx: &PolicyContextNapi) -> bool {
    let data = Box::new(data);
    policies::catalog_needs_partition(&data, ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn catalog_needs_pruned_recompose(data: Value, ctx: &PolicyContextNapi) -> bool {
    let data = Box::new(data);
    policies::catalog_needs_pruned_recompose(&data, ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn request_pass_through(ctx: &PolicyContextNapi, tools: Vec<Value>) -> bool {
    let tools = tools.into_boxed_slice();
    policies::request_pass_through(ctx_from_napi(ctx), &tools)
}

#[napi]
#[must_use]
pub fn full_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::full_pass_through(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn is_decomposed_tool_root_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_decomposed_tool_root_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_decomposed_optional_property_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_decomposed_optional_property_chunk(&item)
}

#[napi]
pub fn filter_recompose_json_entries(
    json_list: Vec<Value>,
    ctx: &PolicyContextNapi,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Vec<String>>,
) -> Vec<Value> {
    let json_list = json_list.into_boxed_slice();
    policies::filter_recompose_json_entries(
        ctx_from_napi(ctx),
        &json_list,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        llm_selected_paths
            .map(|items| items.into_iter().collect::<HashSet<String>>())
            .as_ref(),
    )
}

#[napi]
#[must_use]
pub fn mitigate_empty_optional_properties(
    entries: Vec<Value>,
    catalog_index: Value,
    ctx: &PolicyContextNapi,
    post_rerank_scored: Option<Value>,
    pipeline: Vec<String>,
) -> Vec<Value> {
    let entries = entries.into_boxed_slice();
    let catalog_index = Box::new(catalog_index);
    let index = catalog_index_from_value(&catalog_index);
    let post_rerank_scored = post_rerank_scored.map(Box::new);
    let pipeline = pipeline.into_boxed_slice();
    policies::mitigate_empty_optional_properties(
        ctx_from_napi(ctx),
        &entries,
        &index,
        post_rerank_scored.as_deref(),
        &pipeline,
    )
}

#[napi]
#[must_use]
pub fn append_description_reinstate_entries(
    entries: Vec<Value>,
    build_catalog: Value,
    catalog_index: Value,
    ctx: &PolicyContextNapi,
) -> Vec<Value> {
    let entries = entries.into_boxed_slice();
    let build_catalog = Box::new(build_catalog);
    let catalog_index = Box::new(catalog_index);
    let index = catalog_index_from_value(&catalog_index);
    policies::append_description_reinstate_entries(
        ctx_from_napi(ctx),
        &entries,
        &build_catalog,
        &index,
    )
}

#[napi]
#[must_use]
pub fn needs_description_reinstate(ctx: &PolicyContextNapi) -> bool {
    policies::needs_description_reinstate(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn is_description_policy(policy: String) -> bool {
    let policy = policy.into_boxed_str();
    let Some(p) = parse_tool_policy(policy.as_ref()) else {
        return false;
    };
    policies::is_description_policy(p)
}

/// # Errors
/// Returns an error when `policy` is not a recognized tool policy name.
#[napi]
pub fn scoring_policy(policy: String) -> Result<String> {
    let policy = policy.into_boxed_str();
    let p = parse_tool_policy(policy.as_ref())
        .ok_or_else(|| Error::from_reason(format!("invalid policy: {policy}")))?;
    Ok(policies::scoring_policy(p).as_str().to_string())
}

#[napi]
#[must_use]
pub fn drop_recomposed_tools_with_empty_properties(
    tools: Vec<Value>,
    catalog_index: Value,
    ctx: &PolicyContextNapi,
) -> Vec<Value> {
    let tools = tools.into_boxed_slice();
    let catalog_index = Box::new(catalog_index);
    let index = catalog_index_from_value(&catalog_index);
    policies::drop_recomposed_tools_with_empty_properties(ctx_from_napi(ctx), &tools, &index)
}

#[napi]
#[must_use]
pub fn extract_json_catalog_document(item: Value) -> Option<String> {
    let item = Box::new(item);
    crate::documents::extract_json_catalog_document(&item)
}

#[napi]
#[must_use]
pub fn extract_md_catalog_document(item: Value) -> Option<String> {
    let item = Box::new(item);
    crate::documents::extract_md_catalog_document(&item)
}

#[napi]
#[must_use]
pub fn extract_document_text(item: Value) -> Option<String> {
    let item = Box::new(item);
    crate::documents::extract_document_text(&item)
}

#[napi]
#[must_use]
pub fn extract_level_info(item: Value) -> Vec<String> {
    let item = Box::new(item);
    crate::documents::extract_level_info(&item)
}

/// # Errors
/// Returns an error when the output directory cannot be created or catalog files cannot be written.
#[napi]
pub fn write_catalog_index(
    index: Value,
    output_dir: Option<String>,
    prune: Option<bool>,
) -> Result<()> {
    let index = Box::new(index);
    let catalog = catalog_index_from_value(&index);
    let output_dir = output_dir.map(std::path::PathBuf::from);
    crate::catalog_io::write_catalog_index_resolved(
        &catalog,
        output_dir.as_deref(),
        prune,
    )
    .map_err(Error::from_reason)
}

#[napi]
#[must_use]
pub fn root_tool_id_from_chunk(item: Value) -> String {
    let item = Box::new(item);
    policies::root_tool_id_from_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_non_system_tool_id(tool_id: String) -> bool {
    let tool_id = tool_id.into_boxed_str();
    policies::is_non_system_tool_id(tool_id.as_ref())
}

#[napi]
#[must_use]
pub fn is_system_tool_id(tool_id: String) -> bool {
    let tool_id = tool_id.into_boxed_str();
    policies::is_system_tool_id(tool_id.as_ref())
}

#[napi]
#[must_use]
pub fn chunk_tool_id(item: Value) -> String {
    let item = Box::new(item);
    policies::chunk_tool_id(&item)
}

#[napi]
#[must_use]
pub fn is_system_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_system_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_non_system_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_non_system_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_system_root_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_system_root_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_mcp_root_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_mcp_root_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_system_optional_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_system_optional_chunk(&item)
}

#[napi]
#[must_use]
pub fn is_mcp_optional_chunk(item: Value) -> bool {
    let item = Box::new(item);
    policies::is_mcp_optional_chunk(&item)
}

#[napi(object)]
pub struct ClassifyOptionalChunksBatchResult {
    pub system: Vec<bool>,
    pub mcp: Vec<bool>,
}

#[napi(js_name = "classifyOptionalChunksBatch")]
#[must_use]
pub fn classify_optional_chunks_batch_napi(items: Vec<Value>) -> ClassifyOptionalChunksBatchResult {
    let items = items.into_boxed_slice();
    let (system, mcp) = policies::classify_optional_chunks_batch(&items);
    ClassifyOptionalChunksBatchResult { system, mcp }
}

#[napi]
#[must_use]
pub fn stash_system_tools(tools: Vec<Value>) -> Vec<Value> {
    let tools = tools.into_boxed_slice();
    policies::stash_system_tools(&tools)
}

#[napi]
#[must_use]
pub fn restore_system_tools(stash: Vec<Value>) -> Vec<Value> {
    let stash = stash.into_boxed_slice();
    policies::restore_system_tools(&stash)
}

#[napi]
#[must_use]
pub fn stash_mcp_tools(tools: Vec<Value>) -> Vec<Value> {
    let tools = tools.into_boxed_slice();
    policies::stash_mcp_tools(&tools)
}

#[napi]
#[must_use]
pub fn restore_mcp_tools(stash: Vec<Value>) -> Vec<Value> {
    let stash = stash.into_boxed_slice();
    policies::restore_mcp_tools(&stash)
}

struct MergeToolMaps {
    pruned_by_name: HashMap<String, Value, RandomState>,
    stashed_by_name: HashMap<String, Value, RandomState>,
}

#[napi]
#[must_use]
pub fn merge_tools_preserving_order(
    original: Vec<Value>,
    pruned_by_name: HashMap<String, Value, RandomState>,
    stashed_by_name: HashMap<String, Value, RandomState>,
) -> Vec<Value> {
    let original = original.into_boxed_slice();
    let maps = MergeToolMaps {
        pruned_by_name,
        stashed_by_name,
    };
    policies::merge_tools_preserving_order(
        &original,
        &maps.pruned_by_name,
        &maps.stashed_by_name,
    )
}

#[napi(object)]
pub struct SplitAnthropicToolsResult {
    pub non_system: Vec<Value>,
    pub system: Vec<Value>,
}

#[napi]
#[must_use]
pub fn split_anthropic_tools(tools: Vec<Value>) -> SplitAnthropicToolsResult {
    let tools = tools.into_boxed_slice();
    let (non_system, system) = policies::split_anthropic_tools(&tools);
    SplitAnthropicToolsResult {
        non_system,
        system,
    }
}

#[napi]
#[must_use]
pub fn entries_for_policy(ctx: &PolicyContextNapi, all_entries: Vec<Value>) -> Vec<Value> {
    let all_entries = all_entries.into_boxed_slice();
    policies::entries_for_policy(ctx_from_napi(ctx), &all_entries)
}

#[napi]
#[must_use]
pub fn tools_for_catalog(ctx: &PolicyContextNapi, tools: Vec<Value>) -> Vec<Value> {
    let tools = tools.into_boxed_slice();
    policies::tools_for_catalog(ctx_from_napi(ctx), &tools)
}

#[napi]
#[must_use]
pub fn system_required_enum_values(data: Value) -> Vec<String> {
    let data = Box::new(data);
    policies::system_required_enum_values(&data)
        .into_iter()
        .collect()
}

#[napi]
#[must_use]
pub fn mcp_required_enum_values(data: Value) -> Vec<String> {
    let data = Box::new(data);
    policies::mcp_required_enum_values(&data)
        .into_iter()
        .collect()
}

#[napi]
#[must_use]
pub fn required_enum_values_by_tool(data: Value) -> HashMap<String, Vec<String>> {
    let data = Box::new(data);
    policies::required_enum_values_by_tool(&data)
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().collect()))
        .collect()
}

#[napi]
#[must_use]
pub fn optional_leaf_survived_rerank(
    item: Value,
    ctx: &PolicyContextNapi,
    rerank_score: Option<f64>,
    llm_selected_paths: Option<Vec<String>>,
) -> bool {
    let item = Box::new(item);
    policies::optional_leaf_survived_rerank(
        ctx_from_napi(ctx),
        &item,
        rerank_score.unwrap_or_else(runtime_config::rerank_score),
        llm_selected_paths
            .map(|items| items.into_iter().collect::<HashSet<String>>())
            .as_ref(),
    )
}

#[napi]
#[must_use]
pub fn needs_partition(ctx: &PolicyContextNapi) -> bool {
    policies::needs_partition(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub const fn needs_pruned_recompose(ctx: &PolicyContextNapi) -> bool {
    policies::needs_pruned_recompose(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn system_tools_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::system_tools_pass_through(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn mcp_tools_pass_through(ctx: &PolicyContextNapi) -> bool {
    policies::mcp_tools_pass_through(ctx_from_napi(ctx))
}

#[napi]
#[must_use]
pub fn anthropic_tool_is_system(tool: Value) -> bool {
    let tool = Box::new(tool);
    policies::anthropic_tool_is_system(&tool)
}

#[napi]
#[must_use]
pub fn anthropic_tool_is_mcp(tool: Value) -> bool {
    let tool = Box::new(tool);
    policies::anthropic_tool_is_mcp(&tool)
}

#[napi]
#[must_use]
pub fn direct_root_optional_chunks_for_tool(items: Vec<Value>, tool_id: String) -> Vec<Value> {
    let items = items.into_boxed_slice();
    let tool_id = tool_id.into_boxed_str();
    policies::direct_root_optional_chunks_for_tool(&items, tool_id.as_ref())
}

#[napi]
#[must_use]
pub fn root_chunk_properties_empty(item: Value) -> bool {
    let item = Box::new(item);
    policies::root_chunk_properties_empty(&item)
}

#[napi]
#[must_use]
pub fn tool_id_has_empty_decomposed_root(catalog_index: Value, tool_id: String) -> bool {
    let catalog_index = Box::new(catalog_index);
    let index = catalog_index_from_value(&catalog_index);
    let tool_id = tool_id.into_boxed_str();
    policies::tool_id_has_empty_decomposed_root(&index, tool_id.as_ref())
}

#[napi]
#[must_use]
pub fn tool_id_had_empty_original_root_properties(
    catalog_index: Value,
    tool_id: String,
) -> bool {
    let catalog_index = Box::new(catalog_index);
    let index = catalog_index_from_value(&catalog_index);
    let tool_id = tool_id.into_boxed_str();
    policies::tool_id_had_empty_original_root_properties(&index, tool_id.as_ref())
}

#[napi]
#[must_use]
pub fn is_direct_root_optional_property_chunk(item: Value) -> bool {
    let item = Box::new(item);
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

    /// # Errors
    /// Does not fail; invalid tool entries are skipped.
    #[napi]
    pub fn add_tool(&mut self, entry: Value) -> Result<()> {
        self.inner.add_tool(entry);
        Ok(())
    }

    #[napi]
    #[must_use]
    pub fn get_tool_info(&self, server_name: String, tool_name: String) -> Option<Value> {
        let server_name = server_name.into_boxed_str();
        let tool_name = tool_name.into_boxed_str();
        self.inner
            .get_tool_info(server_name.as_ref(), tool_name.as_ref())
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

    /// # Errors
    /// Returns an error when the catalog directory cannot be created or files cannot be written.
    #[napi]
    pub fn write_catalog(&mut self) -> Result<CatalogIndexResult> {
        let index = self
            .inner
            .write_catalog()
            .map_err(Error::from_reason)?;
        Ok(CatalogIndexResult {
            tools: index.tools.clone(),
            files: index.files.clone(),
        })
    }

    #[napi]
    pub fn to_catalog_dict(&mut self, catalog_prefix: Option<String>) -> Value {
        match catalog_prefix {
            Some(prefix) => {
                let prefix = prefix.into_boxed_str();
                self.inner.to_catalog_dict_with_prefix(prefix.as_ref())
            }
            None => self.inner.to_catalog_dict(),
        }
    }
}

#[napi(object)]
pub struct CatalogIndexResult {
    pub tools: Vec<Value>,
    pub files: HashMap<String, String>,
}
