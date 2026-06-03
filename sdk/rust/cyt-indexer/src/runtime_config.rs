//! Score thresholds and default policy strings; override from the host app via `configure`.

use std::sync::{OnceLock, RwLock};

/// Runtime defaults for retrieve scoring and policy fallbacks.
#[derive(Clone, Debug, PartialEq)]
pub struct RuntimeConfig {
    pub decomposed_score: f64,
    pub enum_score: f64,
    pub rerank_score: f64,
    pub empty_optional_fallback_k: usize,
    pub default_system_policy: String,
    pub default_mcp_policy: String,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            decomposed_score: 0.5,
            enum_score: 0.2,
            rerank_score: 0.003,
            empty_optional_fallback_k: 3,
            default_system_policy: "prune_optional".to_string(),
            default_mcp_policy: "prune_all".to_string(),
        }
    }
}

fn config_lock() -> &'static RwLock<RuntimeConfig> {
    static CONFIG: OnceLock<RwLock<RuntimeConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| RwLock::new(RuntimeConfig::default()))
}

pub fn configure(cfg: RuntimeConfig) {
    *config_lock().write().expect("runtime config lock") = cfg;
}

pub fn snapshot() -> RuntimeConfig {
    config_lock().read().expect("runtime config lock").clone()
}

pub fn decomposed_score() -> f64 {
    snapshot().decomposed_score
}

pub fn enum_score() -> f64 {
    snapshot().enum_score
}

pub fn rerank_score() -> f64 {
    snapshot().rerank_score
}

pub fn empty_optional_fallback_k() -> usize {
    snapshot().empty_optional_fallback_k
}

pub fn default_system_policy() -> String {
    snapshot().default_system_policy
}

pub fn default_mcp_policy() -> String {
    snapshot().default_mcp_policy
}
