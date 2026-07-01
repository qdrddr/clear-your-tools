use std::collections::{HashMap, HashSet};

use rust_stemmers::{Algorithm, Stemmer};

use super::config::Bm25CohesionConfig;

// Same English stopword list as Tantivy/Lucene (see tantivy StopWordFilter::new(Language::English)).
const ENGLISH_STOPWORDS: &[&str] = &[
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these",
    "they", "this", "to", "was", "will", "with",
];

pub struct TextAnalyzerPipeline {
    use_stopwords: bool,
    stemmer: Stemmer,
    stopwords: HashSet<&'static str>,
}

impl TextAnalyzerPipeline {
    #[must_use]
    pub fn new(config: &Bm25CohesionConfig) -> Self {
        Self {
            use_stopwords: config.use_stopwords,
            stemmer: Stemmer::create(Algorithm::English),
            stopwords: ENGLISH_STOPWORDS.iter().copied().collect(),
        }
    }

    fn process_token(&self, token: &str) -> Option<String> {
        if token.is_empty() {
            return None;
        }
        let lower = token.to_lowercase();
        if self.use_stopwords && self.stopwords.contains(lower.as_str()) {
            return None;
        }
        Some(self.stemmer.stem(&lower).into_owned())
    }

    #[must_use]
    pub fn tokenize(&self, text: &str) -> Vec<String> {
        simple_tokens(text)
            .filter_map(|token| self.process_token(token))
            .collect()
    }

    #[must_use]
    pub fn term_frequencies(&self, text: &str) -> HashMap<String, u32> {
        let mut freqs = HashMap::new();
        for token in self.tokenize(text) {
            *freqs.entry(token).or_insert(0) += 1;
        }
        freqs
    }
}

fn simple_tokens(text: &str) -> impl Iterator<Item = &str> {
    SimpleTokenSplit {
        text,
        start: None,
        cursor: 0,
    }
}

struct SimpleTokenSplit<'a> {
    text: &'a str,
    start: Option<usize>,
    cursor: usize,
}

impl<'a> Iterator for SimpleTokenSplit<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<Self::Item> {
        while let Some(ch) = self.text[self.cursor..].chars().next() {
            let i = self.cursor;
            self.cursor += ch.len_utf8();
            if ch.is_alphanumeric() {
                if self.start.is_none() {
                    self.start = Some(i);
                }
            } else if let Some(start) = self.start.take() {
                return Some(&self.text[start..i]);
            }
        }
        self.start.take().map(|start| &self.text[start..])
    }
}

/// Word-boundary spans preserving original text (for `WordSegmenter` output).
#[must_use]
pub fn simple_word_spans(text: &str, min_chars: usize) -> Vec<(usize, usize)> {
    let bytes = text.as_bytes();
    let mut spans = Vec::new();
    let mut i = 0usize;
    while i < bytes.len() {
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= bytes.len() {
            break;
        }
        let start = i;
        while i < bytes.len() && !bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i - start >= min_chars {
            spans.push((start, i));
        }
    }
    spans
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bm25_cohesion::Bm25CohesionConfig;

    #[test]
    fn english_stopwords_match_tantivy_list() {
        let pipeline = TextAnalyzerPipeline::new(&Bm25CohesionConfig {
            use_stopwords: true,
            ..Default::default()
        });
        let tokens = pipeline.tokenize("the fox is crafty");
        assert_eq!(tokens, vec!["fox", "crafti"]);
    }
}
