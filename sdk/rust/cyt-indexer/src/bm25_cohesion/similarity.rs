use super::config::Bm25CohesionConfig;
use super::scorer::Bm25Scorer;
use super::tokenizer::TextAnalyzerPipeline;
use super::types::{TextUnit, WindowMode};

fn min_max_normalize(values: &[f64]) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if (max - min).abs() < f64::EPSILON {
        return vec![0.5; values.len()];
    }
    values.iter().map(|v| (v - min) / (max - min)).collect()
}

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

#[must_use]
pub fn similarity_curve(
    units: &[TextUnit],
    config: &Bm25CohesionConfig,
    pipeline: &TextAnalyzerPipeline,
) -> Vec<f64> {
    let w = config.similarity_window;
    let n = config.next_unit_size;
    if units.len() <= w {
        return Vec::new();
    }

    let corpus = build_corpus_units(units, config);
    let corpus_refs: Vec<&str> = corpus.iter().map(String::as_str).collect();
    let scorer = Bm25Scorer::from_documents(pipeline, &corpus_refs);

    let mut raw = Vec::new();
    match config.window_mode {
        WindowMode::Sentence => {
            for i in 0..units.len().saturating_sub(w) {
                let query = join_units(&units[i..i + w]);
                let doc_idx = i + w;
                if doc_idx < corpus.len() {
                    raw.push(scorer.score_query_doc(&query, doc_idx));
                }
            }
        }
        WindowMode::Word => {
            let limit = units.len().saturating_sub(w + n - 1);
            for i in 0..limit {
                let query = join_units(&units[i..i + w]);
                let doc = join_units(&units[i + w..i + w + n]);
                raw.push(scorer.score_query_text(&query, &doc));
            }
        }
    }
    min_max_normalize(&raw)
}
