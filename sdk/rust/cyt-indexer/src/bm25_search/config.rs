use std::path::{Path, PathBuf};
use std::sync::{OnceLock, RwLock};

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
        Self {
            index_dir: PathBuf::from("~/.config/cyt/bm25"),
            stem_language: "english".to_string(),
            stopwords: "en".to_string(),
            use_stopwords: true,
            k1: 1.2,
            b: 0.75,
            mmap: true,
        }
    }
}

static CONFIG: OnceLock<RwLock<Bm25SearchConfig>> = OnceLock::new();

fn config_lock() -> &'static RwLock<Bm25SearchConfig> {
    CONFIG.get_or_init(|| RwLock::new(Bm25SearchConfig::default()))
}

pub fn configure(cfg: &Bm25SearchConfig) {
    *config_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = cfg.clone();
    crate::analyzer::configure(&crate::analyzer::Bm25AnalyzerConfig {
        stem_language: cfg.stem_language.clone(),
        stopwords: cfg.stopwords.clone(),
        use_stopwords: cfg.use_stopwords,
        k1: cfg.k1,
        b: cfg.b,
    });
}

#[must_use]
pub fn snapshot() -> Bm25SearchConfig {
    config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
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
