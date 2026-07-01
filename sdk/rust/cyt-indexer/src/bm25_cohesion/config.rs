use serde_json::Value;

use super::token_counter::{TokenCounter, TokenCounterKind, token_counter_for_kind};
use super::types::{IncludeDelimMode, WindowMode};

const DEFAULT_DELIMITERS: &[&str] = &[". ", "! ", "? ", "\n"];

#[derive(Debug, Clone)]
pub struct Bm25CohesionConfig {
    pub window_mode: WindowMode,
    pub threshold: f64,
    pub merge_threshold: f64,
    pub chunk_size: usize,
    pub token_counter: TokenCounterKind,
    pub similarity_window: usize,
    pub next_unit_size: usize,
    pub skip_window: usize,
    pub min_units_per_chunk: usize,
    pub minimum_words: usize,
    pub minimum_sentences: usize,
    pub min_characters_per_sentence: usize,
    pub min_characters_per_word: usize,
    pub delimiters: Vec<String>,
    pub include_delim: IncludeDelimMode,
    pub use_stopwords: bool,
    pub filter_window: usize,
    pub filter_polyorder: usize,
    pub filter_tolerance: f64,
    pub stem_language: StemLanguage,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StemLanguage {
    #[default]
    English,
}

impl Default for Bm25CohesionConfig {
    fn default() -> Self {
        Self::default_for_mode(WindowMode::Sentence)
    }
}

impl Bm25CohesionConfig {
    #[must_use]
    pub fn default_for_mode(mode: WindowMode) -> Self {
        let mut cfg = Self {
            window_mode: mode,
            threshold: 0.8,
            merge_threshold: 0.7,
            chunk_size: 2048,
            token_counter: TokenCounterKind::Tiktoken,
            similarity_window: 3,
            next_unit_size: 1,
            skip_window: 0,
            min_units_per_chunk: 1,
            minimum_words: 10,
            minimum_sentences: 1,
            min_characters_per_sentence: 24,
            min_characters_per_word: 2,
            delimiters: DEFAULT_DELIMITERS
                .iter()
                .map(|s| (*s).to_string())
                .collect(),
            include_delim: IncludeDelimMode::Prev,
            use_stopwords: true,
            filter_window: 5,
            filter_polyorder: 3,
            filter_tolerance: 0.2,
            stem_language: StemLanguage::English,
        };
        cfg.apply_mode_defaults(mode);
        cfg
    }

    pub const fn apply_mode_defaults(&mut self, mode: WindowMode) {
        self.window_mode = mode;
        match mode {
            WindowMode::Sentence => {
                self.similarity_window = 3;
                self.next_unit_size = 1;
                self.min_units_per_chunk = 1;
            }
            WindowMode::Word => {
                self.similarity_window = 500;
                self.next_unit_size = 5;
                self.min_units_per_chunk = 3;
            }
        }
    }

    /// # Errors
    ///
    /// Returns an error when numeric fields are out of range or inconsistent.
    pub fn validate(&self) -> Result<(), String> {
        if self.chunk_size == 0 {
            return Ok(());
        }
        if self.similarity_window == 0 {
            return Err("similarity_window must be positive".to_string());
        }
        if self.min_units_per_chunk == 0 {
            return Err("min_units_per_chunk must be positive".to_string());
        }
        if !(0.0..1.0).contains(&self.threshold) {
            return Err("threshold must be between 0 and 1".to_string());
        }
        if !(0.0..1.0).contains(&self.merge_threshold) {
            return Err("merge_threshold must be between 0 and 1".to_string());
        }
        if !(0.0..1.0).contains(&self.filter_tolerance) {
            return Err("filter_tolerance must be between 0 and 1".to_string());
        }
        if self.filter_window == 0 {
            return Err("filter_window must be positive".to_string());
        }
        if self.filter_polyorder >= self.filter_window {
            return Err("filter_polyorder must be less than filter_window".to_string());
        }
        if self.next_unit_size == 0 {
            return Err("next_unit_size must be positive".to_string());
        }
        Ok(())
    }

    #[must_use]
    pub fn token_counter_impl(&self) -> Box<dyn TokenCounter> {
        token_counter_for_kind(self.token_counter)
    }

    #[must_use]
    pub fn from_partial(value: &Value) -> Self {
        let mode = value
            .get("window_mode")
            .or_else(|| {
                value
                    .get("bm25_cohesion")
                    .and_then(|v| v.get("window_mode"))
            })
            .and_then(|v| parse_window_mode(Some(v)))
            .unwrap_or(WindowMode::Sentence);
        let mut cfg = Self::default_for_mode(mode);
        if let Some(obj) = value.as_object() {
            merge_partial_fields(&mut cfg, Some(obj));
        }
        if let Some(nested) = value.get("bm25_cohesion").and_then(Value::as_object) {
            merge_partial_fields(&mut cfg, Some(nested));
        }
        cfg
    }
}

fn merge_partial_fields(
    cfg: &mut Bm25CohesionConfig,
    obj: Option<&serde_json::Map<String, Value>>,
) {
    let Some(obj) = obj else {
        return;
    };
    if let Some(v) = obj.get("window_mode")
        && let Some(mode) = parse_window_mode(Some(v))
    {
        cfg.window_mode = mode;
    }
    if let Some(v) = obj.get("threshold").and_then(Value::as_f64) {
        cfg.threshold = v;
    }
    if let Some(v) = obj.get("merge_threshold").and_then(Value::as_f64) {
        cfg.merge_threshold = v;
    }
    if let Some(v) = obj.get("chunk_size").and_then(parse_usize) {
        cfg.chunk_size = v;
    }
    if let Some(v) = obj.get("token_counter") {
        cfg.token_counter = parse_token_counter(v);
    }
    if let Some(v) = obj.get("similarity_window").and_then(parse_usize) {
        cfg.similarity_window = v;
    }
    if let Some(v) = obj.get("next_unit_size").and_then(parse_usize) {
        cfg.next_unit_size = v;
    }
    if let Some(v) = obj.get("skip_window").and_then(parse_usize) {
        cfg.skip_window = v;
    }
    if let Some(v) = obj.get("min_units_per_chunk").and_then(parse_usize) {
        cfg.min_units_per_chunk = v;
    }
    if let Some(v) = obj.get("minimum_words").and_then(parse_usize) {
        cfg.minimum_words = v;
    }
    if let Some(v) = obj.get("minimum_sentences").and_then(parse_usize) {
        cfg.minimum_sentences = v;
    }
    if let Some(v) = obj.get("min_characters_per_sentence").and_then(parse_usize) {
        cfg.min_characters_per_sentence = v;
    }
    if let Some(v) = obj.get("min_characters_per_word").and_then(parse_usize) {
        cfg.min_characters_per_word = v;
    }
    if let Some(v) = obj.get("delimiters").and_then(Value::as_array) {
        cfg.delimiters = v
            .iter()
            .filter_map(|d| d.as_str().map(str::to_string))
            .collect();
    }
    if let Some(v) = obj.get("include_delim") {
        cfg.include_delim = parse_include_delim(v);
    }
    if let Some(v) = obj.get("use_stopwords") {
        cfg.use_stopwords = parse_bool(v, cfg.use_stopwords);
    }
    if let Some(v) = obj.get("filter_window").and_then(parse_usize) {
        cfg.filter_window = v;
    }
    if let Some(v) = obj.get("filter_polyorder").and_then(parse_usize) {
        cfg.filter_polyorder = v;
    }
    if let Some(v) = obj.get("filter_tolerance").and_then(Value::as_f64) {
        cfg.filter_tolerance = v;
    }
}

fn parse_usize(v: &Value) -> Option<usize> {
    v.as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .or_else(|| v.as_i64().and_then(|n| usize::try_from(n).ok()))
}

fn parse_bool(v: &Value, default: bool) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::String(s) => matches!(s.to_ascii_lowercase().as_str(), "yes" | "true" | "1"),
        _ => default,
    }
}

fn parse_window_mode(v: Option<&Value>) -> Option<WindowMode> {
    let s = v?.as_str()?.to_ascii_lowercase();
    match s.as_str() {
        "sentence" => Some(WindowMode::Sentence),
        "word" => Some(WindowMode::Word),
        _ => None,
    }
}

fn parse_token_counter(v: &Value) -> TokenCounterKind {
    match v
        .as_str()
        .unwrap_or("tiktoken")
        .to_ascii_lowercase()
        .as_str()
    {
        "character" => TokenCounterKind::Character,
        "approximate" => TokenCounterKind::Approximate,
        _ => TokenCounterKind::Tiktoken,
    }
}

fn parse_include_delim(v: &Value) -> IncludeDelimMode {
    match v.as_str().unwrap_or("prev").to_ascii_lowercase().as_str() {
        "next" => IncludeDelimMode::Next,
        _ => IncludeDelimMode::Prev,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn word_mode_defaults_similarity_window_500() {
        let cfg = Bm25CohesionConfig::default_for_mode(WindowMode::Word);
        assert_eq!(cfg.similarity_window, 500);
        assert_eq!(cfg.next_unit_size, 5);
    }

    #[test]
    fn partial_merge_preserves_unset() {
        let cfg = Bm25CohesionConfig::from_partial(&json!({"skip_window": 2}));
        assert_eq!(cfg.skip_window, 2);
        assert_eq!(cfg.similarity_window, 3);
    }

    #[test]
    fn validate_rejects_bad_filter() {
        let cfg = Bm25CohesionConfig {
            filter_polyorder: 5,
            ..Default::default()
        };
        assert!(cfg.validate().is_err());
    }
}
