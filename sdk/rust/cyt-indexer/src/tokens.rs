use serde_json::Value;
use tiktoken_rs::cl100k_base;

pub fn compact_json(obj: &Value) -> String {
    serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
}

pub fn count_tokens(text: &str) -> usize {
    let bpe = cl100k_base().unwrap();
    bpe.encode_with_special_tokens(text).len()
}

pub fn count_json_tokens(obj: &Value) -> usize {
    count_tokens(&compact_json(obj))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compact_json_no_whitespace() {
        let s = compact_json(&json!({"a": 1, "b": [2, 3]}));
        assert!(!s.contains(' '));
        assert!(s.contains("\"a\""));
    }

    #[test]
    fn count_tokens_nonempty() {
        assert!(count_tokens("hello world") > 0);
    }
}
