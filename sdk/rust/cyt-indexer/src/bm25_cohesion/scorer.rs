use std::collections::HashMap;

use super::tokenizer::TextAnalyzerPipeline;

const K1: f64 = 1.2;
const B: f64 = 0.75;

fn f64_from_u32(n: u32) -> f64 {
    f64::from(n)
}

fn f64_from_usize(n: usize) -> f64 {
    u32::try_from(n).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

fn bm25_tf_component(d_tf: u32, doc_len: f64, avg_doc_len: f64) -> f64 {
    let d_tf_f = f64::from(d_tf);
    let norm = K1.mul_add(1.0 - B + B * doc_len / avg_doc_len, d_tf_f);
    d_tf_f * (K1 + 1.0) / norm
}

pub struct Bm25Scorer<'a> {
    pipeline: &'a TextAnalyzerPipeline,
    num_docs: usize,
    avg_doc_len: f64,
    doc_freq: HashMap<String, usize>,
    doc_lengths: Vec<u32>,
    doc_tfs: Vec<HashMap<String, u32>>,
}

impl<'a> Bm25Scorer<'a> {
    #[must_use]
    pub fn from_documents(pipeline: &'a TextAnalyzerPipeline, documents: &[&str]) -> Self {
        let doc_tfs: Vec<HashMap<String, u32>> = documents
            .iter()
            .map(|doc| pipeline.term_frequencies(doc))
            .collect();
        let doc_lengths: Vec<u32> = doc_tfs
            .iter()
            .map(|tf| tf.values().copied().sum())
            .collect();
        let num_docs = documents.len().max(1);
        let avg_doc_len = if doc_lengths.is_empty() {
            1.0
        } else {
            let sum = f64_from_u32(doc_lengths.iter().copied().sum());
            let count = f64_from_u32(u32::try_from(doc_lengths.len()).unwrap_or(u32::MAX));
            (sum / count).max(1.0)
        };
        let mut doc_freq = HashMap::new();
        for tf in &doc_tfs {
            for term in tf.keys() {
                *doc_freq.entry(term.clone()).or_insert(0) += 1;
            }
        }
        Self {
            pipeline,
            num_docs,
            avg_doc_len,
            doc_freq,
            doc_lengths,
            doc_tfs,
        }
    }

    fn idf(&self, term: &str) -> f64 {
        let df = f64_from_usize(*self.doc_freq.get(term).unwrap_or(&0));
        let n = f64_from_usize(self.num_docs);
        ((n - df + 0.5) / (df + 0.5)).ln_1p()
    }

    /// BM25 score of `query` against document at `doc_idx`.
    #[must_use]
    pub fn score_query_doc(&self, query: &str, doc_idx: usize) -> f64 {
        if doc_idx >= self.doc_tfs.len() {
            return 0.0;
        }
        let query_tf = self.pipeline.term_frequencies(query);
        let doc_tf = &self.doc_tfs[doc_idx];
        let doc_len = f64_from_u32(self.doc_lengths[doc_idx]);
        let mut score = 0.0;
        for (term, &q_tf) in &query_tf {
            let Some(&d_tf) = doc_tf.get(term) else {
                continue;
            };
            let idf = self.idf(term);
            let tf_component = bm25_tf_component(d_tf, doc_len, self.avg_doc_len);
            score = (idf * tf_component).mul_add(f64::from(q_tf), score);
        }
        score
    }

    /// Score query text against a single ad-hoc document string using corpus stats.
    #[must_use]
    pub fn score_query_text(&self, query: &str, document: &str) -> f64 {
        let doc_tf = self.pipeline.term_frequencies(document);
        let doc_len = f64_from_u32(doc_tf.values().copied().sum());
        let query_tf = self.pipeline.term_frequencies(query);
        let mut score = 0.0;
        for (term, &q_tf) in &query_tf {
            let Some(&d_tf) = doc_tf.get(term) else {
                continue;
            };
            let idf = self.idf(term);
            let tf_component = bm25_tf_component(d_tf, doc_len.max(1.0), self.avg_doc_len);
            score = (idf * tf_component).mul_add(f64::from(q_tf), score);
        }
        score
    }
}
