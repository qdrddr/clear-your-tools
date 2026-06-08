use serde_json::Value;

/// Stringify a JSON value for IDs, names, and display (matches Python `str()` on scalars).
#[must_use]
pub fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        other => other.to_string(),
    }
}
