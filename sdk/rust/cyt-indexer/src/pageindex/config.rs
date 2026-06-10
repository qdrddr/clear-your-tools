use serde_json::Value;

use crate::bm25_cohesion::Bm25CohesionConfig;

#[derive(Debug, Clone)]
pub struct PageIndexConfig {
    pub if_add_node_id: bool,
    pub if_add_node_text: bool,
    pub bm25_cohesion: Bm25CohesionConfig,
}

impl Default for PageIndexConfig {
    fn default() -> Self {
        Self {
            if_add_node_id: true,
            if_add_node_text: false,
            bm25_cohesion: Bm25CohesionConfig::default(),
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
        if obj.contains_key("bm25_cohesion") || obj.contains_key("chunk_size") {
            cfg.bm25_cohesion = Bm25CohesionConfig::from_partial(val);
        }
        cfg
    }

    #[must_use]
    pub const fn cohesion_config(&self) -> &Bm25CohesionConfig {
        &self.bm25_cohesion
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
    use crate::bm25_cohesion::WindowMode;
    use serde_json::json;

    #[test]
    fn defaults_match_cyt_yaml() {
        let cfg = PageIndexConfig::default();
        assert!(cfg.if_add_node_id);
        assert!(!cfg.if_add_node_text);
        assert_eq!(cfg.bm25_cohesion.chunk_size, 2048);
    }

    #[test]
    fn from_value_partial_override() {
        let cfg = PageIndexConfig::from_value(&json!({"if_add_node_text": true}));
        assert!(cfg.if_add_node_id);
        assert!(cfg.if_add_node_text);
    }

    #[test]
    fn from_value_bm25_nested() {
        let cfg = PageIndexConfig::from_value(&json!({
            "bm25_cohesion": {"skip_window": 2, "window_mode": "word"}
        }));
        assert_eq!(cfg.bm25_cohesion.skip_window, 2);
        assert_eq!(cfg.bm25_cohesion.window_mode, WindowMode::Word);
        assert_eq!(cfg.bm25_cohesion.similarity_window, 500);
    }

    #[test]
    fn from_value_ignores_unknown_keys() {
        let cfg = PageIndexConfig::from_value(&json!({"if_add_node_summary": "yes"}));
        assert_eq!(cfg.if_add_node_id, PageIndexConfig::default().if_add_node_id);
    }
}
