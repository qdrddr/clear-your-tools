//! Shared BM25 engine: configuration, Tantivy index construction, and corpus scoring.

pub mod config;
pub mod index;

pub use config::{
    Bm25AnalyzerSettings, Bm25CacheSettings, Bm25Config, Bm25SearchConfig, analyzer_snapshot,
    configure, configure_search, expand_index_dir, search_snapshot, snapshot,
};
pub use index::{
    BODY_FIELD, BODY_TOKENIZER, CorpusScorer, body_schema, build_disk_index, build_ram_index,
    register_body_analyzer, score_with_reader,
};
