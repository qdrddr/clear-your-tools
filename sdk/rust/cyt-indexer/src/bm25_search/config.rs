//! BM25 search configuration (re-exported from unified `bm25::config`).

pub use crate::bm25::config::{
    Bm25SearchConfig, configure_search as configure, expand_index_dir, search_snapshot as snapshot,
};
