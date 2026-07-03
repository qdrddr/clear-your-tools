//! Tantivy BM25 scoring helpers (ephemeral in-RAM indexes and full-corpus scores).

use std::collections::HashMap;

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::{STORED, Schema, TEXT};
use tantivy::{Index, IndexReader, doc};

use crate::analyzer;
use crate::cache::{CachePolicy, build_or_open_bm25_index};

const BODY_FIELD: &str = "body";

fn body_schema() -> Schema {
    let mut builder = Schema::builder();
    builder.add_text_field(BODY_FIELD, TEXT | STORED);
    builder.build()
}

fn build_ram_index(texts: &[&str]) -> Result<Index, String> {
    use tantivy::IndexWriter;

    let schema = body_schema();
    let index = Index::create_in_ram(schema.clone());
    let mut writer: IndexWriter = index.writer(15_000_000).map_err(|e| e.to_string())?;
    let body = schema.get_field(BODY_FIELD).map_err(|e| e.to_string())?;
    for text in texts {
        writer
            .add_document(doc!(body => *text))
            .map_err(|e| e.to_string())?;
    }
    writer.commit().map_err(|e| e.to_string())?;
    Ok(index)
}

fn score_with_reader(
    query: &str,
    corpus_len: usize,
    index: &Index,
    reader: &IndexReader,
) -> Result<Vec<f64>, String> {
    let schema = index.schema();
    let body = schema.get_field(BODY_FIELD).map_err(|e| e.to_string())?;
    let searcher = reader.searcher();

    let terms: Vec<String> = analyzer::analyze_text(query);
    if terms.is_empty() {
        return Ok(vec![0.0; corpus_len]);
    }
    let query_str = terms.join(" ");
    let parser = QueryParser::for_index(index, vec![body]);
    let q = parser.parse_query(&query_str).map_err(|e| e.to_string())?;
    let top = searcher
        .search(&q, &TopDocs::with_limit(corpus_len).order_by_score())
        .map_err(|e| e.to_string())?;

    let mut scores = vec![0.0_f64; corpus_len];
    for (score, addr) in top {
        let doc_id = addr.doc_id as usize;
        if doc_id < scores.len() {
            scores[doc_id] = f64::from(score);
        }
    }
    Ok(scores)
}

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
    let index = build_ram_index(corpus)?;
    let schema = index.schema();
    let body = schema.get_field(BODY_FIELD).map_err(|e| e.to_string())?;
    let reader = index.reader().map_err(|e| e.to_string())?;
    let searcher = reader.searcher();

    let doc_idx = corpus
        .iter()
        .position(|&d| d == document)
        .unwrap_or_else(|| corpus.len().saturating_sub(1));

    let terms: Vec<String> = analyzer::analyze_text(query);
    if terms.is_empty() {
        return Ok(0.0);
    }
    let query_str = terms.join(" ");
    let parser = QueryParser::for_index(&index, vec![body]);
    let q = parser.parse_query(&query_str).map_err(|e| e.to_string())?;

    let top = searcher
        .search(&q, &TopDocs::with_limit(corpus.len()).order_by_score())
        .map_err(|e| e.to_string())?;

    for (score, addr) in top {
        if addr.doc_id as usize == doc_idx {
            return Ok(f64::from(score));
        }
    }
    Ok(0.0)
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
