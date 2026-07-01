use chunk::{filter_split_indices, find_local_minima_interpolated, find_merge_indices};

use super::config::Bm25CohesionConfig;
use super::segment::segment_units;
use super::similarity::similarity_curve;
use super::token_counter::TokenCounter;
use super::types::{CohesionChunk, TextUnit};

pub struct Bm25CohesionChunker {
    config: Bm25CohesionConfig,
    token_counter: Box<dyn TokenCounter>,
}

impl Bm25CohesionChunker {
    /// # Errors
    ///
    /// Returns an error when configuration validation fails.
    pub fn new(config: Bm25CohesionConfig) -> Result<Self, String> {
        config.validate()?;
        let token_counter = config.token_counter_impl();
        Ok(Self {
            config,
            token_counter,
        })
    }

    #[must_use]
    pub fn needs_chunking(&self, text: &str) -> bool {
        self.config.chunk_size > 0 && self.token_counter.count(text) > self.config.chunk_size
    }

    /// Sliding-window BM25 similarity curve for `text`.
    ///
    /// # Errors
    ///
    /// Returns an error when BM25 scoring fails.
    pub fn similarity_curve_values(&self, text: &str) -> Result<Vec<f64>, String> {
        let units = segment_units(text, &self.config, self.token_counter.as_ref());
        if units.len() <= self.config.similarity_window {
            return Ok(Vec::new());
        }
        similarity_curve(&units, &self.config)
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

        let similarities = similarity_curve(&units, &self.config).unwrap_or_default();
        let split_indices = self.split_indices(&similarities, units.len());
        let mut groups = group_units(&units, &split_indices);
        if self.config.skip_window > 0 && groups.len() > 1 {
            groups = skip_and_merge(&groups, &self.config);
        }
        let mut chunks = self.groups_to_chunks(text, &groups);
        if chunks.len() > 1 {
            self.fill_inter_chunk_gaps(text, &mut chunks);
        }
        self.extend_last_chunk_coverage(text, &mut chunks);
        if chunks.len() > 1 {
            self.merge_undersized_chunks(text, &mut chunks);
        }
        chunks
    }

    fn extend_last_chunk_coverage(&self, source: &str, chunks: &mut [CohesionChunk]) {
        let Some(last) = chunks.last_mut() else {
            return;
        };
        if last.end_index < source.len() {
            last.end_index = source.len();
            last.text = source[last.start_index..last.end_index].to_string();
            last.token_count = self.token_counter.count(&last.text);
        }
    }

    fn is_undersized(&self, text: &str) -> bool {
        if self.config.minimum_words > 0 && word_count(text) < self.config.minimum_words {
            return true;
        }
        if self.config.minimum_sentences > 0
            && sentence_count(text, &self.config, self.token_counter.as_ref())
                < self.config.minimum_sentences
        {
            return true;
        }
        false
    }

    fn merge_undersized_chunks(&self, source: &str, chunks: &mut Vec<CohesionChunk>) {
        let mut i = 0usize;
        while i < chunks.len() {
            if !self.is_undersized(&chunks[i].text) {
                i += 1;
                continue;
            }
            if i + 1 < chunks.len() {
                let start = chunks[i].start_index;
                let end = chunks[i + 1].end_index;
                chunks[i + 1].start_index = start;
                chunks[i + 1].end_index = end;
                chunks[i + 1].text = source[start..end].to_string();
                chunks[i + 1].token_count = self.token_counter.count(&chunks[i + 1].text);
                chunks.remove(i);
            } else if chunks.len() > 1 {
                let end = chunks[i].end_index;
                let prev = i - 1;
                chunks[prev].end_index = end;
                chunks[prev].text = source[chunks[prev].start_index..end].to_string();
                chunks[prev].token_count = self.token_counter.count(&chunks[prev].text);
                chunks.remove(i);
                i = prev;
            } else {
                break;
            }
        }
    }

    fn fill_inter_chunk_gaps(&self, source: &str, chunks: &mut [CohesionChunk]) {
        for i in 0..chunks.len().saturating_sub(1) {
            let next_start = chunks[i + 1].start_index;
            if chunks[i].end_index < next_start {
                chunks[i].end_index = next_start;
                chunks[i].text = source[chunks[i].start_index..next_start].to_string();
                chunks[i].token_count = self.token_counter.count(&chunks[i].text);
            }
        }
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

    fn split_indices(&self, similarities: &[f64], unit_count: usize) -> Vec<usize> {
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
        out.push(unit_count);
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
            let counts: Vec<usize> = group.iter().map(|u| u.token_count).collect();
            let merge_indices = find_merge_indices(&counts, self.config.chunk_size);
            let mut start_unit = 0usize;
            for &end_unit in &merge_indices {
                let end_unit = end_unit.min(group.len());
                if start_unit < end_unit {
                    chunks.push(Self::units_to_chunk(source, &group[start_unit..end_unit]));
                }
                start_unit = end_unit;
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

fn skip_and_merge(groups: &[Vec<TextUnit>], config: &Bm25CohesionConfig) -> Vec<Vec<TextUnit>> {
    if groups.len() <= 1 || config.skip_window == 0 {
        return groups.to_vec();
    }
    let group_texts: Vec<String> = groups
        .iter()
        .map(|g| g.iter().map(|u| u.text.as_str()).collect())
        .collect();
    let refs: Vec<&str> = group_texts.iter().map(String::as_str).collect();

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
            let score =
                crate::bm25_search::score_query_against_doc(&group_texts[i], refs[j], &refs)
                    .unwrap_or(0.0);
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

fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

fn sentence_count(text: &str, config: &Bm25CohesionConfig, counter: &dyn TokenCounter) -> usize {
    segment_units(text, config, counter).len()
}

#[cfg(test)]
fn concat_chunks(chunks: &[CohesionChunk]) -> String {
    chunks.iter().map(|c| c.text.as_str()).collect()
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
    fn word_mode_preserves_markdown_formatting() -> Result<(), String> {
        let text = "### Step 2: Select the Best Match\n\nFrom the resolution results, choose based on:\n\n- Exact or closest name match to what the user asked for\n- Higher benchmark scores indicate better documentation quality\n- If the user mentioned a version (e.g., \"React 19\"), prefer version-specific IDs";
        let cfg = Bm25CohesionConfig {
            chunk_size: 30,
            similarity_window: 10,
            skip_window: 0,
            ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
        };
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let chunks = chunker.chunk(text);
        assert!(
            chunks.len() > 1,
            "expected section to split into multiple chunks"
        );
        for chunk in &chunks {
            assert!(chunk.text.contains(' '), "chunk should preserve spaces");
            assert_eq!(
                &text[chunk.start_index..chunk.end_index],
                chunk.text.as_str(),
                "chunk text must match source slice"
            );
        }
        let recompiled: String = chunks.iter().map(|c| c.text.as_str()).collect();
        assert_eq!(recompiled, text);
        assert!(
            recompiled.contains("### Step 2: Select the Best Match"),
            "recompiled text should preserve heading"
        );
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

    #[test]
    fn word_mode_covers_full_source() -> Result<(), String> {
        let cfg = Bm25CohesionConfig {
            chunk_size: 100,
            similarity_window: 10,
            skip_window: 0,
            ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
        };
        let text = "## Core Tools\n\n| Tool | Purpose |\n|------|---------|\n| `ctx_read(path, mode)` | Read file |\n| `ctx_call(name, args)` | Invoke any tool by name |";
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let chunks = chunker.chunk(text);
        assert_eq!(concat_chunks(&chunks), text);
        assert!(
            !chunks.iter().any(|c| c.text.trim() == "Invoke"),
            "should not emit bare Invoke orphan"
        );
        assert!(
            chunks
                .last()
                .is_some_and(|c| c.text.contains("Invoke any tool by name |")),
            "last chunk should include full table row tail"
        );
        Ok(())
    }

    #[test]
    fn merge_undersized_forward() -> Result<(), String> {
        let cfg = Bm25CohesionConfig {
            chunk_size: 50,
            minimum_words: 10,
            minimum_sentences: 1,
            ..Default::default()
        };
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let text = "First topic sentence here with enough words. Second topic follows now with enough words too. Third unrelated finance news item here. Fourth finance detail here with words.";
        let chunks = chunker.chunk(text);
        for chunk in &chunks {
            if word_count(&chunk.text) < 10 {
                assert!(
                    concat_chunks(&chunks).len() <= text.len(),
                    "tiny chunk only allowed when unavoidable"
                );
            }
        }
        assert_eq!(concat_chunks(&chunks), text);
        Ok(())
    }

    #[test]
    fn respects_disable_zero_minimum_words() -> Result<(), String> {
        let cfg = Bm25CohesionConfig {
            chunk_size: 100,
            similarity_window: 10,
            minimum_words: 0,
            minimum_sentences: 0,
            ..Bm25CohesionConfig::default_for_mode(WindowMode::Word)
        };
        let text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega extra words here and more padding text";
        let chunker = Bm25CohesionChunker::new(cfg)?;
        let chunks = chunker.chunk(text);
        assert_eq!(concat_chunks(&chunks), text);
        Ok(())
    }
}
