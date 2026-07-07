//! Shared Tantivy BM25 index construction and in-memory corpus scoring.

use std::path::Path;

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::{IndexRecordOption, Schema, TextFieldIndexing, TextOptions};
use tantivy::{Index, IndexReader, IndexWriter, doc};

pub const BODY_FIELD: &str = "body";
pub const BODY_TOKENIZER: &str = "cyt_bm25";

const WRITER_HEAP_BYTES: usize = 15_000_000;

/// Build the single-field schema used for BM25 corpus indexes.
#[must_use]
pub fn body_schema() -> Schema {
    let text_indexing = TextFieldIndexing::default()
        .set_tokenizer(BODY_TOKENIZER)
        .set_index_option(IndexRecordOption::WithFreqsAndPositions);
    let body_options = TextOptions::default()
        .set_indexing_options(text_indexing)
        .set_stored();
    let mut builder = Schema::builder();
    builder.add_text_field(BODY_FIELD, body_options);
    builder.build()
}

/// Register the configured English analyzer on *index* for document and query tokenization.
pub fn register_body_analyzer(index: &Index) {
    index
        .tokenizers()
        .register(BODY_TOKENIZER, crate::analyzer::registered_text_analyzer());
}

fn prepare_index(index: Index) -> Index {
    register_body_analyzer(&index);
    index
}

fn add_corpus_documents(index: &Index, texts: &[&str], writer: &IndexWriter) -> Result<(), String> {
    let schema = index.schema();
    let body = schema.get_field(BODY_FIELD).map_err(|e| e.to_string())?;
    for text in texts {
        writer
            .add_document(doc!(body => *text))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Build an ephemeral in-RAM index for *texts* using the shared analyzer.
///
/// # Errors
///
/// Returns an error when Tantivy index construction fails.
pub fn build_ram_index(texts: &[&str]) -> Result<Index, String> {
    let schema = body_schema();
    let index = prepare_index(Index::create_in_ram(schema));
    let mut writer: IndexWriter = index.writer(WRITER_HEAP_BYTES).map_err(|e| e.to_string())?;
    add_corpus_documents(&index, texts, &writer)?;
    writer.commit().map_err(|e| e.to_string())?;
    Ok(index)
}

/// Build a persisted Tantivy index at *dir* for *texts*.
///
/// # Errors
///
/// Returns an error when the directory cannot be created or indexing fails.
pub fn build_disk_index(texts: &[&str], dir: &Path) -> Result<Index, String> {
    if dir.exists() {
        std::fs::remove_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let schema = body_schema();
    let index = prepare_index(Index::create_in_dir(dir, schema).map_err(|e| e.to_string())?);
    let mut writer: IndexWriter = index.writer(WRITER_HEAP_BYTES).map_err(|e| e.to_string())?;
    add_corpus_documents(&index, texts, &writer)?;
    writer.commit().map_err(|e| e.to_string())?;
    Ok(index)
}

/// Reusable in-memory BM25 scorer for a fixed corpus (build index once, score many queries).
pub struct CorpusScorer {
    index: Index,
    reader: IndexReader,
    corpus_len: usize,
}

impl CorpusScorer {
    /// Build a scorer over *corpus* document texts.
    ///
    /// # Errors
    ///
    /// Returns an error when the Tantivy index cannot be built.
    pub fn from_corpus(corpus: &[&str]) -> Result<Self, String> {
        if corpus.is_empty() {
            return Err("corpus must not be empty".to_string());
        }
        let index = build_ram_index(corpus)?;
        let reader = index.reader().map_err(|e| e.to_string())?;
        Ok(Self {
            index,
            reader,
            corpus_len: corpus.len(),
        })
    }

    /// Score each document in the corpus against *query*.
    ///
    /// # Errors
    ///
    /// Returns an error when the query cannot be parsed or executed.
    pub fn score_corpus(&self, query: &str) -> Result<Vec<f64>, String> {
        score_with_reader(query, self.corpus_len, &self.index, &self.reader)
    }

    /// Score *query* against the corpus document equal to *document*.
    ///
    /// # Errors
    ///
    /// Returns an error when the query cannot be parsed or executed.
    pub fn score_query_against_doc(
        &self,
        query: &str,
        document: &str,
        corpus: &[&str],
    ) -> Result<f64, String> {
        if self.corpus_len == 0 {
            return Ok(0.0);
        }
        let doc_idx = corpus
            .iter()
            .position(|&d| d == document)
            .unwrap_or_else(|| self.corpus_len.saturating_sub(1));
        let scores = self.score_corpus(query)?;
        Ok(scores.get(doc_idx).copied().unwrap_or(0.0))
    }
}

/// Score each document in `corpus_len` slots against `query` using an existing index reader.
///
/// # Errors
///
/// Returns an error when the query cannot be parsed or executed.
pub fn score_with_reader(
    query: &str,
    corpus_len: usize,
    index: &Index,
    reader: &IndexReader,
) -> Result<Vec<f64>, String> {
    if corpus_len == 0 {
        return Ok(Vec::new());
    }
    let schema = index.schema();
    let body = schema.get_field(BODY_FIELD).map_err(|e| e.to_string())?;
    let searcher = reader.searcher();

    let terms: Vec<String> = crate::analyzer::analyze_text(query);
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
