use chunk::{filter_split_indices, find_local_minima_interpolated, merge_splits};

use super::config::Bm25CohesionConfig;
use super::scorer::Bm25Scorer;
use super::segment::segment_units;
use super::similarity::similarity_curve;
use super::token_counter::TokenCounter;
use super::tokenizer::TextAnalyzerPipeline;
use super::types::{CohesionChunk, TextUnit};

pub struct Bm25CohesionChunker {
    config: Bm25CohesionConfig,
    token_counter: Box<dyn TokenCounter>,
    pipeline: TextAnalyzerPipeline,
}

impl Bm25CohesionChunker {
    /// # Errors
    ///
    /// Returns an error when configuration validation fails.
    pub fn new(config: Bm25CohesionConfig) -> Result<Self, String> {
        config.validate()?;
        let token_counter = config.token_counter_impl();
        let pipeline = TextAnalyzerPipeline::new(&config);
        Ok(Self {
            config,
            token_counter,
            pipeline,
        })
    }

    #[must_use]
    pub fn needs_chunking(&self, text: &str) -> bool {
        self.config.chunk_size > 0 && self.token_counter.count(text) > self.config.chunk_size
    }

    #[must_use]
    pub fn chunk(&self, text: &str) -> Vec<CohesionChunk> {
        if text.trim().is_empty() {
            return Vec::new();
        }
        if self.config.chunk_size == 0 {
            return vec![self.single_chunk(text, 0, text.len())];
        }
        let total = self.token_counter.count(text);
        if total <= self.config.chunk_size {
            return vec![self.single_chunk(text, 0, text.len())];
        }

        let units = segment_units(text, &self.config, self.token_counter.as_ref());
        if units.is_empty() {
            return vec![self.single_chunk(text, 0, text.len())];
        }
        if units.len() <= self.config.similarity_window {
            return vec![Self::units_to_chunk(text, &units)];
        }

        let similarities = similarity_curve(&units, &self.config, &self.pipeline);
        let split_indices = self.split_indices(&similarities);
        let mut groups = group_units(&units, &split_indices);
        if self.config.skip_window > 0 && groups.len() > 1 {
            groups = skip_and_merge(&groups, &self.config, &self.pipeline);
        }
        self.groups_to_chunks(text, &groups)
    }

    #[must_use]
    pub fn chunk_batch(&self, texts: &[&str]) -> Vec<Vec<CohesionChunk>> {
        texts.iter().map(|t| self.chunk(t)).collect()
    }

    fn single_chunk(&self, text: &str, start: usize, end: usize) -> CohesionChunk {
        CohesionChunk {
            text: text.to_string(),
            start_index: start,
            end_index: end,
            token_count: self.token_counter.count(text),
        }
    }

    fn units_to_chunk(source: &str, units: &[TextUnit]) -> CohesionChunk {
        let start = units.first().map_or(0, |u| u.start_index);
        let end = units.last().map_or(source.len(), |u| u.end_index);
        let text = source[start..end].to_string();
        let token_count = units.iter().map(|u| u.token_count).sum();
        CohesionChunk {
            text,
            start_index: start,
            end_index: end,
            token_count,
        }
    }

    fn split_indices(&self, similarities: &[f64]) -> Vec<usize> {
        if similarities.is_empty() || similarities.len() < self.config.filter_window {
            return Vec::new();
        }
        let Some(minima) = find_local_minima_interpolated(
            similarities,
            self.config.filter_window,
            self.config.filter_polyorder,
            self.config.filter_tolerance,
        ) else {
            return Vec::new();
        };
        if minima.indices.is_empty() {
            return Vec::new();
        }
        let filtered = filter_split_indices(
            &minima.indices,
            &minima.values,
            self.config.threshold,
            self.config.min_units_per_chunk,
        );
        if filtered.indices.is_empty() {
            return Vec::new();
        }
        let w = self.config.similarity_window;
        let mut out = vec![0];
        out.extend(filtered.indices.iter().map(|&i| i + w));
        out.push(similarities.len() + w);
        out
    }

    fn groups_to_chunks(&self, source: &str, groups: &[Vec<TextUnit>]) -> Vec<CohesionChunk> {
        let mut chunks = Vec::new();
        for group in groups {
            if group.is_empty() {
                continue;
            }
            let group_tokens: usize = group.iter().map(|u| u.token_count).sum();
            if group_tokens <= self.config.chunk_size {
                chunks.push(Self::units_to_chunk(source, group));
                continue;
            }
            let texts: Vec<&str> = group.iter().map(|u| u.text.as_str()).collect();
            let counts: Vec<usize> = group.iter().map(|u| u.token_count).collect();
            let merged = merge_splits(&texts, &counts, self.config.chunk_size);
            let mut byte_cursor = group[0].start_index;
            for (piece, &tok_count) in merged.merged.iter().zip(merged.token_counts.iter()) {
                let len = piece.len();
                chunks.push(CohesionChunk {
                    text: (*piece).clone(),
                    start_index: byte_cursor,
                    end_index: byte_cursor + len,
                    token_count: tok_count,
                });
                byte_cursor += len;
            }
        }
        if chunks.is_empty() {
            chunks.push(self.single_chunk(source, 0, source.len()));
        }
        chunks
    }
}

fn group_units(units: &[TextUnit], split_indices: &[usize]) -> Vec<Vec<TextUnit>> {
    if split_indices.is_empty() {
        return vec![units.to_vec()];
    }
    let mut groups = Vec::new();
    for window in split_indices.windows(2) {
        let start = window[0];
        let end = window[1];
        if start < end && end <= units.len() {
            groups.push(units[start..end].to_vec());
        }
    }
    if groups.is_empty() && !units.is_empty() {
        groups.push(units.to_vec());
    }
    groups
}

fn skip_and_merge(
    groups: &[Vec<TextUnit>],
    config: &Bm25CohesionConfig,
    pipeline: &TextAnalyzerPipeline,
) -> Vec<Vec<TextUnit>> {
    if groups.len() <= 1 || config.skip_window == 0 {
        return groups.to_vec();
    }
    let group_texts: Vec<String> = groups
        .iter()
        .map(|g| g.iter().map(|u| u.text.as_str()).collect())
        .collect();
    let refs: Vec<&str> = group_texts.iter().map(String::as_str).collect();
    let scorer = Bm25Scorer::from_documents(pipeline, &refs);

    let mut merged_groups = Vec::new();
    let mut i = 0usize;
    while i < groups.len() {
        if i == groups.len() - 1 {
            merged_groups.push(groups[i].clone());
            break;
        }
        let skip_index = (i + config.skip_window + 1).min(groups.len() - 1);
        let mut best_score = -1.0_f64;
        let mut best_idx = None;
        for j in (i + 1)..=skip_index {
            let score = scorer.score_query_doc(&group_texts[i], j);
            if score >= config.merge_threshold && score > best_score {
                best_score = score;
                best_idx = Some(j);
            }
        }
        if let Some(j) = best_idx {
            let mut merged = Vec::new();
            for group in &groups[i..=j] {
                merged.extend(group.clone());
            }
            merged_groups.push(merged);
            i = j + 1;
        } else {
            merged_groups.push(groups[i].clone());
            i += 1;
        }
    }
    merged_groups
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bm25_cohesion::types::WindowMode;

    #[test]
    fn small_text_single_chunk() -> Result<(), String> {
        let chunker = Bm25CohesionChunker::new(Bm25CohesionConfig::default())?;
        let chunks = chunker.chunk("Short text.");
        assert_eq!(chunks.len(), 1);
        assert!(chunks[0].text.contains("Short"));
        Ok(())
    }

    #[test]
    fn empty_returns_empty() -> Result<(), String> {
        let chunker = Bm25CohesionChunker::new(Bm25CohesionConfig::default())?;
        assert!(chunker.chunk("   ").is_empty());
        Ok(())
    }

    #[test]
    fn determinism() -> Result<(), String> {
        let cfg = Bm25CohesionConfig {
            chunk_size: 50,
            ..Default::default()
        };
        let text = "First topic sentence here. Second topic follows now. Third unrelated finance news. Fourth finance detail here.";
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let a = chunker.chunk(text);
        let b = chunker.chunk(text);
        assert_eq!(a.len(), b.len());
        for (x, y) in a.iter().zip(b.iter()) {
            assert_eq!(x.text, y.text);
        }
        Ok(())
    }

    #[test]
    fn word_mode_runs() -> Result<(), String> {
        let cfg = Bm25CohesionConfig {
            chunk_size: 30,
            similarity_window: 10,
            ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
        };
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let mut words = String::new();
        for i in 0..100 {
            use std::fmt::Write;
            let _ = write!(words, "word{i} ");
        }
        let chunks = chunker.chunk(&words);
        assert!(!chunks.is_empty());
        Ok(())
    }
}
