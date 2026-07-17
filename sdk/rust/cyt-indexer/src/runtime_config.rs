//! Runtime scoring defaults — synced to `chunk-your-tools`.

pub use chunk_your_tools::runtime_config::{
    RuntimeConfig, decomposed_score, default_mcp_policy, default_system_policy,
    empty_optional_fallback_k, enum_score, rerank_score, snapshot,
};

pub fn configure(cfg: RuntimeConfig) {
    chunk_your_tools::runtime_config::configure(cfg);
}
