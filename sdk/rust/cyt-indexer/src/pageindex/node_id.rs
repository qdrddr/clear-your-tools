use serde_json::Value;

/// Parse a node-id token such as `"3"`, `"0003"`, or `"12"`.
///
/// # Errors
///
/// Returns an error when the token is not a valid unsigned integer.
pub fn parse_node_id_token(token: &str) -> Result<u32, String> {
    token
        .trim()
        .parse::<u32>()
        .map_err(|_| format!("invalid node_id '{token}'"))
}

/// Read a node id from a JSON value (number or legacy zero-padded string).
#[must_use]
pub fn node_id_from_value(v: Option<&Value>) -> u32 {
    match v {
        Some(Value::Number(n)) => n.as_u64().and_then(|u| u32::try_from(u).ok()).unwrap_or(0),
        Some(Value::String(s)) => parse_node_id_token(s).unwrap_or(0),
        _ => 0,
    }
}

/// Serialize a node id as a JSON number.
#[must_use]
pub fn node_id_value(id: u32) -> Value {
    Value::Number(id.into())
}

/// File-system key for decomposed node markdown (`0`, `1`, …).
#[must_use]
pub fn node_id_key(id: u32) -> String {
    id.to_string()
}
