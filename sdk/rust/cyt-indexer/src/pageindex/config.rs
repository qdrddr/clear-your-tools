use serde_json::{Value, json};

use crate::bm25_cohesion::Bm25CohesionConfig;

#[derive(Debug, Clone)]
pub struct PageIndexConfig {
    pub if_add_node_id: bool,
    pub if_add_node_text: bool,
    /// When false with `chunk_size > 0`, each node still gets one full-text chunk file
    /// (no BM25 splitting). Same outcome as `chunk_size: 0`.
    pub enable_bm25_chunking: bool,
    pub bm25_cohesion: Bm25CohesionConfig,
}

impl Default for PageIndexConfig {
    fn default() -> Self {
        Self {
            if_add_node_id: true,
            if_add_node_text: false,
            enable_bm25_chunking: true,
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
        if let Some(v) = obj.get("enable_bm25_chunking") {
            cfg.enable_bm25_chunking = parse_bool(v, cfg.enable_bm25_chunking);
        }
        if obj.contains_key("bm25_cohesion") || obj.contains_key("chunk_size") {
            cfg.bm25_cohesion = Bm25CohesionConfig::from_partial(val);
        }
        cfg
    }

    /// Whether BM25 cohesion splitting runs (vs one full-text chunk per node).
    #[must_use]
    pub const fn bm25_splitting_enabled(&self) -> bool {
        self.enable_bm25_chunking && self.bm25_cohesion.chunk_size > 0
    }

    /// Cohesion settings for chunk attachment; forces `chunk_size: 0` when splitting is off.
    #[must_use]
    pub fn cohesion_config_for_chunking(&self) -> Bm25CohesionConfig {
        let mut cfg = self.bm25_cohesion.clone();
        if !self.bm25_splitting_enabled() {
            cfg.chunk_size = 0;
        }
        cfg
    }

    #[must_use]
    pub const fn cohesion_config(&self) -> &Bm25CohesionConfig {
        &self.bm25_cohesion
    }

    #[must_use]
    pub fn without_bm25_chunking() -> Self {
        Self {
            enable_bm25_chunking: false,
            ..Self::default()
        }
    }

    /// Alias for [`Self::without_bm25_chunking`] — one full-text chunk per node, no splitting.
    #[must_use]
    pub fn one_chunk_per_node() -> Self {
        Self::without_bm25_chunking()
    }

    /// Serialize page-index settings stored in chunk variant metadata.
    #[must_use]
    pub fn to_index_params_value(&self) -> Value {
        json!({
            "if_add_node_id": self.if_add_node_id,
            "if_add_node_text": self.if_add_node_text,
            "enable_bm25_chunking": self.enable_bm25_chunking,
            "bm25_cohesion": self.bm25_cohesion.to_value(),
        })
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
        assert!(cfg.enable_bm25_chunking);
        assert!(cfg.bm25_splitting_enabled());
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
        assert_eq!(
            cfg.if_add_node_id,
            PageIndexConfig::default().if_add_node_id
        );
    }

    #[test]
    fn enable_bm25_chunking_false_disables_splitting_only() {
        let cfg = PageIndexConfig::from_value(&json!({"enable_bm25_chunking": false}));
        assert!(!cfg.bm25_splitting_enabled());
        assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
    }

    #[test]
    fn chunk_size_zero_disables_splitting() {
        let cfg = PageIndexConfig::from_value(&json!({"chunk_size": 0}));
        assert!(!cfg.bm25_splitting_enabled());
        assert_eq!(cfg.bm25_cohesion.chunk_size, 0);
        assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
    }

    #[test]
    fn without_bm25_chunking_helper() {
        let cfg = PageIndexConfig::without_bm25_chunking();
        assert!(!cfg.bm25_splitting_enabled());
        assert_eq!(cfg.cohesion_config_for_chunking().chunk_size, 0);
    }
}
