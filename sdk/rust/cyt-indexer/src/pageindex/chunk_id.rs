use serde_json::Value;

/// Parse a chunk id token (decimal string).
///
/// # Errors
///
/// Returns an error when the token is not a valid unsigned integer.
pub fn parse_chunk_id_token(token: &str) -> Result<u32, String> {
    let trimmed = token.trim();
    if trimmed.is_empty() {
        return Err("empty chunk_id".to_string());
    }
    trimmed
        .parse::<u32>()
        .map_err(|_| format!("invalid chunk_id '{token}'"))
}

#[must_use]
pub fn chunk_id_from_value(value: Option<&Value>) -> u32 {
    value
        .and_then(|v| {
            v.as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0)
}

/// Next monotonic chunk id after existing chunk ids in structure (starts at 0).
#[must_use]
pub fn next_chunk_id(structure: &Value) -> u32 {
    let mut max_id = 0u32;
    let mut found = false;
    walk_structure(structure, &mut |obj| {
        if let Some(Value::Array(chunks)) = obj.get("chunks") {
            for chunk in chunks {
                if let Some(obj) = chunk.as_object() {
                    let id = chunk_id_from_value(obj.get("chunk_id"));
                    max_id = max_id.max(id);
                    found = true;
                }
            }
        }
    });
    if found {
        max_id.saturating_add(1)
    } else {
        0
    }
}

fn walk_structure(structure: &Value, visit: &mut impl FnMut(&serde_json::Map<String, Value>)) {
    match structure {
        Value::Object(map) => {
            visit(map);
            if let Some(Value::Array(children)) = map.get("nodes") {
                for child in children {
                    walk_structure(child, visit);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                walk_structure(item, visit);
            }
        }
        _ => {}
    }
}

/// Parse chunk id specs (same format as node ids).
///
/// # Errors
///
/// Returns an error when a spec is invalid.
pub fn parse_chunk_ids(spec: &str) -> Result<Vec<u32>, String> {
    let mut result = Vec::new();
    for part in spec.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some((start, end)) = part.split_once('-') {
            let start = parse_chunk_id_token(start)?;
            let end = parse_chunk_id_token(end)?;
            if start > end {
                return Err(format!("invalid chunk_id range '{part}': start must be <= end"));
            }
            result.extend(start..=end);
        } else {
            result.push(parse_chunk_id_token(part)?);
        }
    }
    result.sort_unstable();
    result.dedup();
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn next_chunk_starts_at_zero_without_chunks() {
        let structure = json!([
            {"node_id": 0},
            {"node_id": 4},
        ]);
        assert_eq!(next_chunk_id(&structure), 0);
    }

    #[test]
    fn next_chunk_after_existing_chunks() {
        let structure = json!([
            {"node_id": 4, "chunks": [{"chunk_id": 8}]},
        ]);
        assert_eq!(next_chunk_id(&structure), 9);
    }
}
