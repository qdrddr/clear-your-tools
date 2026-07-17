//! Tool catalog retrieval — re-exported from `chunk-your-tools` with token-aware catalog dict helpers.

pub use chunk_your_tools::retrieve::*;

use crate::build::{catalog_index_from_value, catalog_index_to_catalog_dict};
use serde_json::Value;

/// Resolve the full build catalog dict used for reinstatement and enum metadata.
pub fn resolve_build_catalog(catalog: &Value, survivor_data: &Value) -> Value {
    if catalog.get("tools").is_some() && catalog.get("files").is_some() {
        return catalog_index_to_catalog_dict(&catalog_index_from_value(catalog));
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
