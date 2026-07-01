use super::config::Bm25CohesionConfig;
use super::types::{TextUnit, WindowMode};
use crate::bm25_search::{NormalizeMode, normalize_scores, score_corpus, score_query_against_doc};

fn join_units(units: &[TextUnit]) -> String {
    units.iter().map(|u| u.text.as_str()).collect()
}

fn build_corpus_units(units: &[TextUnit], config: &Bm25CohesionConfig) -> Vec<String> {
    match config.window_mode {
        WindowMode::Sentence => units.iter().map(|u| u.text.clone()).collect(),
        WindowMode::Word => {
            let n = config.next_unit_size;
            let mut docs = Vec::new();
            if units.len() <= n {
                if !units.is_empty() {
                    docs.push(join_units(units));
                }
                return docs;
            }
            for i in 0..=units.len().saturating_sub(n) {
                docs.push(join_units(&units[i..i + n]));
            }
            docs
        }
    }
}

/// Sliding-window BM25 similarity curve (min-max normalized).
///
/// # Errors
///
/// Returns an error when BM25 scoring fails.
pub fn similarity_curve(
    units: &[TextUnit],
    config: &Bm25CohesionConfig,
) -> Result<Vec<f64>, String> {
    let w = config.similarity_window;
    let n = config.next_unit_size;
    if units.len() <= w {
        return Ok(Vec::new());
    }

    let corpus = build_corpus_units(units, config);
    let corpus_refs: Vec<&str> = corpus.iter().map(String::as_str).collect();

    let mut raw = Vec::new();
    match config.window_mode {
        WindowMode::Sentence => {
            for i in 0..units.len().saturating_sub(w) {
                let query = join_units(&units[i..i + w]);
                let doc_idx = i + w;
                if doc_idx < corpus.len() {
                    let doc = corpus[doc_idx].as_str();
                    raw.push(score_query_against_doc(&query, doc, &corpus_refs)?);
                }
            }
        }
        WindowMode::Word => {
            let limit = units.len().saturating_sub(w + n - 1);
            for i in 0..limit {
                let query = join_units(&units[i..i + w]);
                let doc = join_units(&units[i + w..i + w + n]);
                raw.push(score_query_against_doc(&query, &doc, &corpus_refs)?);
            }
        }
    }
    Ok(normalize_scores(&raw, NormalizeMode::MinMax))
}

/// Score a query against each document in corpus (for SDPM merge pass).
///
/// # Errors
///
/// Returns an error when BM25 scoring fails.
pub fn score_groups(query: &str, groups: &[&str]) -> Result<Vec<f64>, String> {
    score_corpus(query, groups)
}
