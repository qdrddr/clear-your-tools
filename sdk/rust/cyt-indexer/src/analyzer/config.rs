#[derive(Debug, Clone, PartialEq)]
pub struct Bm25AnalyzerConfig {
    pub stem_language: String,
    pub stopwords: String,
    pub use_stopwords: bool,
    pub k1: f64,
    pub b: f64,
}

impl Default for Bm25AnalyzerConfig {
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

pub fn configure(cfg: &Bm25AnalyzerConfig) {
    super::configure_and_refresh(cfg);
}

#[must_use]
pub fn snapshot() -> Bm25AnalyzerConfig {
    super::analyzer_config_snapshot()
}
