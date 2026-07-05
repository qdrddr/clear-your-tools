//! In-memory cache tuning (lazy registry, async disk, LRU capacities).

use std::sync::{LazyLock, RwLock};

use serde_json::Value;

#[derive(Debug, Clone)]
pub struct MemoryCacheConfig {
    pub lazy_registry: bool,
    pub async_disk_writes: bool,
    pub lru_chunk_bodies: usize,
    pub lru_merged_documents: usize,
    pub lru_skills_index: usize,
    pub lru_tantivy_indexes: usize,
    pub lru_tool_catalogs: usize,
}

impl Default for MemoryCacheConfig {
    fn default() -> Self {
        Self {
            lazy_registry: false,
            async_disk_writes: true,
            lru_chunk_bodies: 512,
            lru_merged_documents: 128,
            lru_skills_index: 64,
            lru_tantivy_indexes: 32,
            lru_tool_catalogs: 128,
        }
    }
}

static MEMORY_CACHE_CONFIG: LazyLock<RwLock<MemoryCacheConfig>> =
    LazyLock::new(|| RwLock::new(MemoryCacheConfig::from_env()));

impl MemoryCacheConfig {
    #[must_use]
    pub fn from_env() -> Self {
        let mut cfg = Self::default();
        if let Ok(raw) = std::env::var("CYT_CACHE_LAZY_REGISTRY") {
            cfg.lazy_registry = matches!(raw.trim(), "1" | "true" | "yes");
        }
        if let Ok(raw) = std::env::var("CYT_CACHE_ASYNC_DISK") {
            cfg.async_disk_writes = matches!(raw.trim(), "1" | "true" | "yes");
        }
        cfg.lru_chunk_bodies = env_usize("CYT_CACHE_LRU_CHUNK_BODIES", cfg.lru_chunk_bodies);
        cfg.lru_merged_documents =
            env_usize("CYT_CACHE_LRU_MERGED_DOCUMENTS", cfg.lru_merged_documents);
        cfg.lru_skills_index = env_usize("CYT_CACHE_LRU_SKILLS_INDEX", cfg.lru_skills_index);
        cfg.lru_tantivy_indexes =
            env_usize("CYT_CACHE_LRU_TANTIVY_INDEXES", cfg.lru_tantivy_indexes);
        cfg.lru_tool_catalogs = env_usize("CYT_CACHE_LRU_TOOL_CATALOGS", cfg.lru_tool_catalogs);
        cfg
    }

    fn apply_json(&mut self, value: &Value) {
        if let Some(v) = value.get("lazy_registry").and_then(Value::as_bool) {
            self.lazy_registry = v;
        }
        if let Some(v) = value.get("async_disk_writes").and_then(Value::as_bool) {
            self.async_disk_writes = v;
        }
        if let Some(v) = value.get("lru_chunk_bodies").and_then(Value::as_u64) {
            self.lru_chunk_bodies = usize::try_from(v).unwrap_or(self.lru_chunk_bodies);
        }
        if let Some(v) = value.get("lru_merged_documents").and_then(Value::as_u64) {
            self.lru_merged_documents = usize::try_from(v).unwrap_or(self.lru_merged_documents);
        }
        if let Some(v) = value.get("lru_skills_index").and_then(Value::as_u64) {
            self.lru_skills_index = usize::try_from(v).unwrap_or(self.lru_skills_index);
        }
        if let Some(v) = value.get("lru_tantivy_indexes").and_then(Value::as_u64) {
            self.lru_tantivy_indexes = usize::try_from(v).unwrap_or(self.lru_tantivy_indexes);
        }
        if let Some(v) = value.get("lru_tool_catalogs").and_then(Value::as_u64) {
            self.lru_tool_catalogs = usize::try_from(v).unwrap_or(self.lru_tool_catalogs);
        }
        if let Some(lru) = value.get("lru").and_then(Value::as_object) {
            if let Some(v) = lru.get("chunk_bodies").and_then(Value::as_u64) {
                self.lru_chunk_bodies = usize::try_from(v).unwrap_or(self.lru_chunk_bodies);
            }
            if let Some(v) = lru.get("merged_documents").and_then(Value::as_u64) {
                self.lru_merged_documents = usize::try_from(v).unwrap_or(self.lru_merged_documents);
            }
            if let Some(v) = lru.get("skills_index").and_then(Value::as_u64) {
                self.lru_skills_index = usize::try_from(v).unwrap_or(self.lru_skills_index);
            }
            if let Some(v) = lru.get("tantivy_indexes").and_then(Value::as_u64) {
                self.lru_tantivy_indexes = usize::try_from(v).unwrap_or(self.lru_tantivy_indexes);
            }
            if let Some(v) = lru.get("tool_catalogs").and_then(Value::as_u64) {
                self.lru_tool_catalogs = usize::try_from(v).unwrap_or(self.lru_tool_catalogs);
            }
        }
    }
}

fn env_usize(name: &str, default: usize) -> usize {
    std::env::var(name)
        .ok()
        .and_then(|raw| raw.trim().parse().ok())
        .unwrap_or(default)
}

#[must_use]
pub fn memory_cache_config() -> MemoryCacheConfig {
    MEMORY_CACHE_CONFIG
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

pub fn set_memory_cache_config(cfg: MemoryCacheConfig) {
    if let Ok(mut guard) = MEMORY_CACHE_CONFIG.write() {
        *guard = cfg;
    }
}

/// Apply JSON config from Python (`cache.memory` block).
pub fn configure_memory_cache(value: &Value) {
    let mut cfg = MemoryCacheConfig::from_env();
    cfg.apply_json(value);
    set_memory_cache_config(cfg);
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn apply_json_nested_lru() {
        let mut cfg = MemoryCacheConfig::default();
        cfg.apply_json(&json!({
            "lazy_registry": false,
            "lru": { "chunk_bodies": 64 }
        }));
        assert!(!cfg.lazy_registry);
        assert_eq!(cfg.lru_chunk_bodies, 64);
    }
}
