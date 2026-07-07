//! Tantivy BM25 scoring helpers (ephemeral in-RAM indexes and full-corpus scores).

use std::collections::HashMap;

use crate::analyzer;
use crate::bm25::index::{CorpusScorer, build_ram_index, score_with_reader};
use crate::cache::{CachePolicy, build_or_open_bm25_index};

/// Score each document in `corpus` against `query`, using disk cache when *fingerprint* is set.
///
/// # Errors
///
/// Returns an error when the Tantivy index cannot be built or queried.
pub fn score_corpus_cached(
    query: &str,
    corpus: &[&str],
    fingerprint: &str,
) -> Result<Vec<f64>, String> {
    if corpus.is_empty() {
        return Ok(Vec::new());
    }
    let handle = build_or_open_bm25_index(corpus, fingerprint, CachePolicy::Auto)?.data;
    score_with_reader(query, corpus.len(), handle.index(), handle.reader())
}

/// Score each document in `corpus` against `query` using Tantivy BM25 (full corpus).
///
/// Returns one score per corpus document (`DocId` order).
///
/// # Errors
///
/// Returns an error when the ephemeral Tantivy index cannot be built or queried.
pub fn score_corpus(query: &str, corpus: &[&str]) -> Result<Vec<f64>, String> {
    if corpus.is_empty() {
        return Ok(Vec::new());
    }
    let index = build_ram_index(corpus)?;
    let reader = index.reader().map_err(|e| e.to_string())?;
    score_with_reader(query, corpus.len(), &index, &reader)
}

/// Score `query` against `document` using corpus statistics from `corpus` texts.
///
/// # Errors
///
/// Returns an error when the ephemeral Tantivy index cannot be built or queried.
pub fn score_query_against_doc(
    query: &str,
    document: &str,
    corpus: &[&str],
) -> Result<f64, String> {
    if corpus.is_empty() {
        return Ok(0.0);
    }
    let scorer = CorpusScorer::from_corpus(corpus)?;
    scorer.score_query_against_doc(query, document, corpus)
}

/// Term-frequency map via shared analyzer (for legacy parity tests).
#[must_use]
pub fn term_frequencies(text: &str) -> HashMap<String, u32> {
    analyzer::term_frequencies(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scores_nonempty_corpus() -> Result<(), String> {
        let corpus = ["alpha beta gamma", "alpha beta different"];
        let scores = score_corpus("alpha beta", &corpus)?;
        assert_eq!(scores.len(), 2);
        assert!(scores[0] >= scores[1]);
        Ok(())
    }
}
