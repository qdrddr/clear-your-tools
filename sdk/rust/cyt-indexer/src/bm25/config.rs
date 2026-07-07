//! Unified BM25 configuration (analyzer + on-disk cache).

use std::path::{Path, PathBuf};
use std::sync::{OnceLock, RwLock};

use crate::analyzer::Bm25AnalyzerConfig;

/// Analyzer/tokenization and BM25 formula parameters.
///
/// `k1` and `b` are persisted for fingerprinting and future Tantivy support; Tantivy 0.26
/// still uses its built-in defaults (`1.2` / `0.75`) at scoring time.
#[derive(Debug, Clone, PartialEq)]
pub struct Bm25AnalyzerSettings {
    pub stem_language: String,
    pub stopwords: String,
    pub use_stopwords: bool,
    pub k1: f64,
    pub b: f64,
}

impl Default for Bm25AnalyzerSettings {
    fn default() -> Self {
        Self {
            stem_language: "english".to_string(),
            stopwords: "en".to_string(),
            use_stopwords: true,
            k1: 1.2,
            b: 0.75,
        }
    }
}

impl From<&Bm25AnalyzerSettings> for Bm25AnalyzerConfig {
    fn from(value: &Bm25AnalyzerSettings) -> Self {
        Self {
            stem_language: value.stem_language.clone(),
            stopwords: value.stopwords.clone(),
            use_stopwords: value.use_stopwords,
            k1: value.k1,
            b: value.b,
        }
    }
}

/// Disk cache layout for catalog BM25 indexes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bm25CacheSettings {
    pub index_dir: PathBuf,
    pub mmap: bool,
}

impl Default for Bm25CacheSettings {
    fn default() -> Self {
        Self {
            index_dir: PathBuf::from("~/.config/cyt/bm25"),
            mmap: true,
        }
    }
}

/// Full BM25 runtime configuration.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Bm25Config {
    pub analyzer: Bm25AnalyzerSettings,
    pub cache: Bm25CacheSettings,
}

/// Back-compat flattened view used by search bindings and FFI.
#[derive(Debug, Clone, PartialEq)]
pub struct Bm25SearchConfig {
    pub index_dir: PathBuf,
    pub stem_language: String,
    pub stopwords: String,
    pub use_stopwords: bool,
    pub k1: f64,
    pub b: f64,
    pub mmap: bool,
}

impl Default for Bm25SearchConfig {
    fn default() -> Self {
        Self::from(&Bm25Config::default())
    }
}

impl From<&Bm25Config> for Bm25SearchConfig {
    fn from(cfg: &Bm25Config) -> Self {
        Self {
            index_dir: cfg.cache.index_dir.clone(),
            stem_language: cfg.analyzer.stem_language.clone(),
            stopwords: cfg.analyzer.stopwords.clone(),
            use_stopwords: cfg.analyzer.use_stopwords,
            k1: cfg.analyzer.k1,
            b: cfg.analyzer.b,
            mmap: cfg.cache.mmap,
        }
    }
}

impl From<&Bm25SearchConfig> for Bm25Config {
    fn from(cfg: &Bm25SearchConfig) -> Self {
        Self {
            analyzer: Bm25AnalyzerSettings {
                stem_language: cfg.stem_language.clone(),
                stopwords: cfg.stopwords.clone(),
                use_stopwords: cfg.use_stopwords,
                k1: cfg.k1,
                b: cfg.b,
            },
            cache: Bm25CacheSettings {
                index_dir: cfg.index_dir.clone(),
                mmap: cfg.mmap,
            },
        }
    }
}

static CONFIG: OnceLock<RwLock<Bm25Config>> = OnceLock::new();

fn config_lock() -> &'static RwLock<Bm25Config> {
    CONFIG.get_or_init(|| RwLock::new(Bm25Config::default()))
}

pub fn configure(cfg: &Bm25Config) {
    *config_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = cfg.clone();
    crate::analyzer::configure(&Bm25AnalyzerConfig::from(&cfg.analyzer));
}

pub fn configure_search(cfg: &Bm25SearchConfig) {
    configure(&Bm25Config::from(cfg));
}

#[must_use]
pub fn snapshot() -> Bm25Config {
    config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

#[must_use]
pub fn search_snapshot() -> Bm25SearchConfig {
    Bm25SearchConfig::from(&snapshot())
}

#[must_use]
pub fn analyzer_snapshot() -> Bm25AnalyzerSettings {
    snapshot().analyzer
}

#[must_use]
pub fn expand_index_dir(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if s.starts_with("~/")
        && let Some(home) = dirs_home()
    {
        return home.join(s.trim_start_matches("~/"));
    }
    path.to_path_buf()
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}
