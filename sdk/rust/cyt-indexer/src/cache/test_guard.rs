//! Serialize cache config mutations in unit tests (global `MEMORY_CACHE_CONFIG`).

use std::sync::{LazyLock, Mutex, MutexGuard};

use serde_json::Value;

use super::config::{MemoryCacheConfig, configure_memory_cache, memory_cache_config};

static CACHE_CONFIG_TEST_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));

pub struct CacheConfigTestGuard {
    _lock: MutexGuard<'static, ()>,
    prev: MemoryCacheConfig,
}

impl CacheConfigTestGuard {
    pub fn with_patch(patch: &Value) -> Self {
        let lock = CACHE_CONFIG_TEST_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let prev = memory_cache_config();
        configure_memory_cache(patch);
        Self { _lock: lock, prev }
    }
}

impl Drop for CacheConfigTestGuard {
    fn drop(&mut self) {
        super::config::set_memory_cache_config(self.prev.clone());
    }
}
