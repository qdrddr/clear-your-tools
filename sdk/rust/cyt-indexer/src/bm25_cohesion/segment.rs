use chunk::{split_at_patterns, IncludeDelim};

use super::config::Bm25CohesionConfig;
use super::token_counter::TokenCounter;
use super::tokenizer::simple_word_spans;
use super::types::{IncludeDelimMode, TextUnit, WindowMode};

pub trait UnitSegmenter {
    fn segment(&self, text: &str, config: &Bm25CohesionConfig, counter: &dyn TokenCounter) -> Vec<TextUnit>;
}

pub struct SentenceSegmenter;

impl UnitSegmenter for SentenceSegmenter {
    fn segment(&self, text: &str, config: &Bm25CohesionConfig, counter: &dyn TokenCounter) -> Vec<TextUnit> {
        if text.trim().is_empty() {
            return Vec::new();
        }
        let include = match config.include_delim {
            IncludeDelimMode::Prev => IncludeDelim::Prev,
            IncludeDelimMode::Next => IncludeDelim::Next,
        };
        let patterns: Vec<Vec<u8>> = config
            .delimiters
            .iter()
            .map(|d| d.as_bytes().to_vec())
            .collect();
        let pattern_refs: Vec<&[u8]> = patterns.iter().map(Vec::as_slice).collect();
        let offsets = split_at_patterns(
            text.as_bytes(),
            &pattern_refs,
            include,
            config.min_characters_per_sentence,
        );
        let mut units = Vec::new();
        for (start, end) in offsets {
            let slice = &text[start..end];
            if slice.trim().is_empty() {
                continue;
            }
            units.push(TextUnit {
                text: slice.to_string(),
                start_index: start,
                end_index: end,
                token_count: counter.count(slice),
            });
        }
        if units.is_empty() && !text.trim().is_empty() {
            units.push(TextUnit {
                text: text.to_string(),
                start_index: 0,
                end_index: text.len(),
                token_count: counter.count(text),
            });
        }
        units
    }
}

pub struct WordSegmenter;

impl UnitSegmenter for WordSegmenter {
    fn segment(&self, text: &str, config: &Bm25CohesionConfig, counter: &dyn TokenCounter) -> Vec<TextUnit> {
        if text.trim().is_empty() {
            return Vec::new();
        }
        simple_word_spans(text, config.min_characters_per_word)
            .into_iter()
            .map(|(start, end)| {
                let slice = &text[start..end];
                TextUnit {
                    text: slice.to_string(),
                    start_index: start,
                    end_index: end,
                    token_count: counter.count(slice),
                }
            })
            .collect()
    }
}

#[must_use]
pub fn segment_units(text: &str, config: &Bm25CohesionConfig, counter: &dyn TokenCounter) -> Vec<TextUnit> {
    match config.window_mode {
        WindowMode::Sentence => SentenceSegmenter.segment(text, config, counter),
        WindowMode::Word => WordSegmenter.segment(text, config, counter),
    }
}
