use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageIndexConfig {
    pub if_add_node_id: bool,
    pub if_add_node_text: bool,
}

impl Default for PageIndexConfig {
    fn default() -> Self {
        Self {
            if_add_node_id: true,
            if_add_node_text: false,
        }
    }
}

impl PageIndexConfig {
    #[must_use]
    pub fn from_value(val: &Value) -> Self {
        let mut cfg = Self::default();
        let Some(obj) = val.as_object() else {
            return cfg;
        };
        if let Some(v) = obj.get("if_add_node_id") {
            cfg.if_add_node_id = parse_bool(v, cfg.if_add_node_id);
        }
        if let Some(v) = obj.get("if_add_node_text") {
            cfg.if_add_node_text = parse_bool(v, cfg.if_add_node_text);
        }
        cfg
    }
}

fn parse_bool(v: &Value, default: bool) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::String(s) => matches!(s.to_ascii_lowercase().as_str(), "yes" | "true" | "1"),
        _ => default,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn defaults_match_cyt_yaml() {
        let cfg = PageIndexConfig::default();
        assert!(cfg.if_add_node_id);
        assert!(!cfg.if_add_node_text);
    }

    #[test]
    fn from_value_partial_override() {
        let cfg = PageIndexConfig::from_value(&json!({"if_add_node_text": true}));
        assert!(cfg.if_add_node_id);
        assert!(cfg.if_add_node_text);
    }

    #[test]
    fn from_value_ignores_unknown_keys() {
        let cfg = PageIndexConfig::from_value(&json!({"if_add_node_summary": "yes"}));
        assert_eq!(cfg, PageIndexConfig::default());
    }
}
