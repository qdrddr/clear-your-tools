use crate::tiktoken;

/// Legacy approximate estimate (kept for explicit `approximate` config).
#[must_use]
pub fn approximate_token_count(text: &str) -> usize {
    text.chars()
        .map(|c| if c.is_ascii() { 1u32 } else { 2 })
        .sum::<u32>() as usize
        / 2
        + 1
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TokenCounterKind {
    #[default]
    Tiktoken,
    Approximate,
    Character,
}

pub trait TokenCounter: Send + Sync {
    fn count(&self, text: &str) -> usize;
    fn count_batch(&self, texts: &[&str]) -> Vec<usize> {
        texts.iter().map(|t| self.count(t)).collect()
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct TiktokenCounter;

impl TokenCounter for TiktokenCounter {
    fn count(&self, text: &str) -> usize {
        tiktoken::count_tokens_or_min(text)
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct ApproximateTokenCounter;

impl TokenCounter for ApproximateTokenCounter {
    fn count(&self, text: &str) -> usize {
        approximate_token_count(text)
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct CharacterTokenCounter;

impl TokenCounter for CharacterTokenCounter {
    fn count(&self, text: &str) -> usize {
        text.chars().count().max(1)
    }
}

#[must_use]
pub fn token_counter_for_kind(kind: TokenCounterKind) -> Box<dyn TokenCounter> {
    match kind {
        TokenCounterKind::Tiktoken => Box::new(TiktokenCounter),
        TokenCounterKind::Approximate => Box::new(ApproximateTokenCounter),
        TokenCounterKind::Character => Box::new(CharacterTokenCounter),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn approximate_matches_legacy_formula() {
        assert_eq!(approximate_token_count(""), 1);
        assert_eq!(approximate_token_count("hello"), 3);
    }

    #[test]
    fn tiktoken_counts_nonzero() {
        let counter = TiktokenCounter;
        assert!(counter.count("hello world") >= 1);
    }
}
