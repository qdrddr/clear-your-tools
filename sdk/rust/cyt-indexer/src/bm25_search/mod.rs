mod catalog;
mod config;
mod normalize;
mod skills;
mod tantivy_score;

pub use catalog::{
    CatalogDocument, ScoreCatalogOptions, catalog_fingerprint, collect_catalog_documents,
    index_path_for_catalog, score_catalog_dict, score_catalog_in_place,
};
pub use config::{Bm25SearchConfig, configure, expand_index_dir, snapshot};
pub use normalize::{NormalizeMode, exp_similarity, min_max_normalize, normalize_scores};
pub use skills::{
    batch_reconstruct_skill_matches, bm25_frontmatter_gate, bm25_search_skill_chunks,
    greedy_select_skill_items,
};
pub use tantivy_score::{
    score_corpus, score_corpus_cached, score_query_against_doc, term_frequencies,
};
