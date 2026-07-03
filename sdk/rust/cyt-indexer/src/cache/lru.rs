//! Simple in-process LRU cache (no external dependency).

use std::collections::HashMap;
use std::hash::Hash;

#[derive(Debug)]
pub struct LruCache<K, V> {
    map: HashMap<K, (V, u64)>,
    tick: u64,
    capacity: usize,
}

impl<K, V> LruCache<K, V>
where
    K: Eq + Hash + Clone,
{
    #[must_use]
    pub fn new(capacity: usize) -> Self {
        Self {
            map: HashMap::new(),
            tick: 0,
            capacity: capacity.max(1),
        }
    }

    pub fn get(&mut self, key: &K) -> Option<&V> {
        if !self.map.contains_key(key) {
            return None;
        }
        self.tick = self.tick.wrapping_add(1);
        let tick = self.tick;
        if let Some(entry) = self.map.get_mut(key) {
            entry.1 = tick;
            return Some(&entry.0);
        }
        None
    }

    pub fn get_cloned(&mut self, key: &K) -> Option<V>
    where
        V: Clone,
    {
        self.get(key).cloned()
    }

    pub fn insert(&mut self, key: K, value: V) -> Option<V> {
        self.tick = self.tick.wrapping_add(1);
        let tick = self.tick;
        if self.map.len() >= self.capacity && !self.map.contains_key(&key) {
            self.evict_one();
        }
        self.map.insert(key, (value, tick)).map(|(v, _)| v)
    }

    fn evict_one(&mut self) {
        let Some(lru_key) = self
            .map
            .iter()
            .min_by_key(|(_, (_, tick))| *tick)
            .map(|(k, _)| k.clone())
        else {
            return;
        };
        self.map.remove(&lru_key);
    }

    #[cfg(test)]
    #[must_use]
    pub fn len(&self) -> usize {
        self.map.len()
    }
}
