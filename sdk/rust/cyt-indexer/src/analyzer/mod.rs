//! Shared Tantivy English text analyzer for BM25 search and cohesion.

mod config;

pub use config::{Bm25AnalyzerConfig, configure, snapshot};

use std::sync::{OnceLock, RwLock};

use tantivy::tokenizer::{
    Language, LowerCaser, SimpleTokenizer, Stemmer, StopWordFilter, TextAnalyzer,
};

static CONFIG: OnceLock<RwLock<Bm25AnalyzerConfig>> = OnceLock::new();
static ANALYZER: OnceLock<RwLock<TextAnalyzer>> = OnceLock::new();

fn config_lock() -> &'static RwLock<Bm25AnalyzerConfig> {
    CONFIG.get_or_init(|| RwLock::new(Bm25AnalyzerConfig::default()))
}

fn analyzer_lock() -> &'static RwLock<TextAnalyzer> {
    ANALYZER.get_or_init(|| RwLock::new(build_analyzer(&Bm25AnalyzerConfig::default())))
}

fn build_analyzer(cfg: &Bm25AnalyzerConfig) -> TextAnalyzer {
    let base = TextAnalyzer::builder(SimpleTokenizer::default()).filter(LowerCaser);
    match (cfg.use_stopwords, cfg.stem_language.as_str()) {
        (true, "english") => match StopWordFilter::new(Language::English) {
            Some(filter) => base
                .filter(filter)
                .filter(Stemmer::new(Language::English))
                .build(),
            None => base.filter(Stemmer::new(Language::English)).build(),
        },
        (true, _) => match StopWordFilter::new(Language::English) {
            Some(filter) => base.filter(filter).build(),
            None => base.build(),
        },
        (false, "english") => base.filter(Stemmer::new(Language::English)).build(),
        (false, _) => base.build(),
    }
}

pub fn configure_and_refresh(cfg: &Bm25AnalyzerConfig) {
    *config_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = cfg.clone();
    *analyzer_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = build_analyzer(cfg);
}

#[must_use]
pub fn analyzer_config_snapshot() -> Bm25AnalyzerConfig {
    config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

/// Clone of the active Tantivy text analyzer for index registration.
#[must_use]
pub fn registered_text_analyzer() -> TextAnalyzer {
    analyzer_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

/// Tokenize text into analyzed terms (stemmed, stopword-filtered when enabled).
#[must_use]
#[allow(clippy::significant_drop_tightening)] // write guard must outlive `token_stream`.
pub fn analyze_text(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut analyzer = analyzer_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let mut stream = analyzer.token_stream(text);
    while stream.advance() {
        let token = stream.token();
        if !token.text.is_empty() {
            tokens.push(token.text.clone());
        }
    }
    tokens
}

/// Term frequencies for analyzed tokens.
#[must_use]
pub fn term_frequencies(text: &str) -> std::collections::HashMap<String, u32> {
    let mut freqs = std::collections::HashMap::new();
    for token in analyze_text(text) {
        *freqs.entry(token).or_insert(0) += 1;
    }
    freqs
}
