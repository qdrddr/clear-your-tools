//! Tool catalog build — delegates to `chunk-your-tools` with tiktoken metadata enrichment.

use std::collections::HashMap;

use chunk_your_tools::build as upstream;
use serde_json::Value;

use crate::paths;
use crate::token_enrichment::{catalog_dict_with_tokens, enrich_tool_schema_metadata};

pub type CatalogIndex = upstream::CatalogIndex;

#[must_use]
pub fn catalog_index_from_value(val: &Value) -> CatalogIndex {
    upstream::catalog_index_from_value(val)
}

pub use upstream::{
    catalog_tool_count, decompose_tool_schema, dedupe_enums, tool_schema_metadata_from_files,
};

#[must_use]
pub fn build_catalog_index(tools: &[Value], all_enums: &[Value]) -> CatalogIndex {
    let mut index = upstream::build_catalog_index(tools, all_enums);
    enrich_tool_schema_metadata(&mut index.files);
    index
}

#[must_use]
pub fn catalog_index_to_catalog_dict(index: &CatalogIndex) -> Value {
    catalog_dict_with_tokens(&index.files, &index.tools, &paths::catalog_prefix())
}

#[must_use]
pub fn catalog_index_to_catalog_dict_with_prefix(
    index: &CatalogIndex,
    catalog_prefix: &str,
) -> Value {
    catalog_dict_with_tokens(&index.files, &index.tools, catalog_prefix)
}

/// Return cached full/decomposed tool schema token metadata when present.
#[must_use]
pub fn catalog_index_tool_schema_metadata(index: &CatalogIndex) -> Value {
    upstream::tool_schema_metadata_from_files(&index.files)
}

/// Construct a catalog index value (for tests and cache merge helpers).
#[must_use]
#[allow(clippy::implicit_hasher, clippy::missing_const_for_fn)]
pub fn catalog_index_new(tools: Vec<Value>, files: HashMap<String, String>) -> CatalogIndex {
    CatalogIndex { tools, files }
}
