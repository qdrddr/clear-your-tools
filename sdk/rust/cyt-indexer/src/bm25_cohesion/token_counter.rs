/// Rough `cl100k_base` token estimate (ASCII chars weighted 1, others 2, `/2 + 1`).
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
        text.chars().count()
    }
}

#[must_use]
pub fn token_counter_for_kind(kind: TokenCounterKind) -> Box<dyn TokenCounter> {
    match kind {
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
}
