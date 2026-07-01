//! LLM token counting via `tiktoken-rs` (default `cl100k_base`).

mod config;

pub use config::{AllowedSpecial, TiktokenConfig, configure, snapshot};

use std::sync::{OnceLock, RwLock};

use tiktoken_rs::CoreBPE;

static CONFIG: OnceLock<RwLock<TiktokenConfig>> = OnceLock::new();
static BPE: OnceLock<RwLock<Option<CoreBPE>>> = OnceLock::new();

fn config_lock() -> &'static RwLock<TiktokenConfig> {
    CONFIG.get_or_init(|| RwLock::new(TiktokenConfig::default()))
}

fn bpe_lock() -> &'static RwLock<Option<CoreBPE>> {
    BPE.get_or_init(|| RwLock::new(None))
}

fn load_bpe(cfg: &TiktokenConfig) -> Result<CoreBPE, String> {
    match cfg.encoding.as_str() {
        "cl100k_base" => tiktoken_rs::cl100k_base().map_err(|e| e.to_string()),
        "o200k_base" => tiktoken_rs::o200k_base().map_err(|e| e.to_string()),
        "p50k_base" => tiktoken_rs::p50k_base().map_err(|e| e.to_string()),
        "r50k_base" | "gpt2" => tiktoken_rs::r50k_base().map_err(|e| e.to_string()),
        other => Err(format!("unsupported tiktoken encoding: {other}")),
    }
}

fn refresh_bpe_cache() -> Result<(), String> {
    let cfg = config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone();
    let bpe = load_bpe(&cfg)?;
    *bpe_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(bpe);
    Ok(())
}

pub fn configure_and_refresh(cfg: TiktokenConfig) {
    *config_lock()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = cfg;
    let _ = refresh_bpe_cache();
}

#[must_use]
pub fn config_snapshot() -> TiktokenConfig {
    config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

#[allow(clippy::significant_drop_tightening)] // `bpe` borrows the read guard for the duration of `f`.
fn with_bpe<R>(f: impl FnOnce(&CoreBPE, &TiktokenConfig) -> R) -> Result<R, String> {
    {
        let guard = bpe_lock()
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if guard.is_none() {
            drop(guard);
            refresh_bpe_cache()?;
        }
    }
    let cfg = config_lock()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone();
    let result = {
        let bpe_guard = bpe_lock()
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let bpe = bpe_guard
            .as_ref()
            .ok_or_else(|| "tiktoken BPE not initialized".to_string())?;
        f(bpe, &cfg)
    };
    Ok(result)
}

fn encode(text: &str) -> Result<Vec<usize>, String> {
    with_bpe(|bpe, cfg| {
        let raw = match cfg.allowed_special {
            AllowedSpecial::All => bpe.encode_with_special_tokens(text),
            AllowedSpecial::None => bpe.encode_ordinary(text),
        };
        raw.into_iter().map(|t| t as usize).collect()
    })
}

/// Count tokens under the configured encoding.
///
/// # Errors
///
/// Returns an error when encoding initialization fails.
pub fn count_tokens(text: &str) -> Result<usize, String> {
    Ok(encode(text)?.len())
}

/// Infallible token count for hot paths (returns 1 on failure).
#[must_use]
pub fn count_tokens_or_min(text: &str) -> usize {
    count_tokens(text).unwrap_or(1).max(1)
}

/// Count tokens for many strings.
///
/// # Errors
///
/// Returns an error when encoding initialization fails.
pub fn count_tokens_batch(texts: &[&str]) -> Result<Vec<usize>, String> {
    texts.iter().map(|t| count_tokens(t)).collect()
}

/// Compact JSON (no spaces) and return its token count.
///
/// # Errors
///
/// Returns an error when serialization or encoding fails.
pub fn count_json_tokens(value: &serde_json::Value) -> Result<usize, String> {
    let compact = serde_json::to_string(value).map_err(|e| e.to_string())?;
    count_tokens(&compact)
}

/// Truncate text to at most `max_tokens`, preferring a word boundary.
///
/// # Errors
///
/// Returns an error when encoding initialization fails.
pub fn truncate_description(description: &str, max_tokens: usize) -> Result<String, String> {
    if description.is_empty() {
        return Ok(String::new());
    }
    if count_tokens(description)? <= max_tokens {
        return Ok(description.to_string());
    }

    let suffix = "...";
    let suffix_tokens = count_tokens(suffix)?;
    let body_budget = max_tokens.saturating_sub(suffix_tokens);
    if body_budget == 0 {
        return Ok(suffix.to_string());
    }

    let chars: Vec<char> = description.chars().collect();
    let mut lo = 0usize;
    let mut hi = chars.len();
    while lo < hi {
        let mid = (lo + hi).div_ceil(2);
        let slice: String = chars[..mid].iter().collect();
        if count_tokens(&slice)? <= body_budget {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }

    let mut body: String = chars[..lo].iter().collect();
    if let Some(sp) = body.rfind(' ')
        && sp > 0
    {
        body.truncate(sp);
    }

    Ok(format!("{body}{suffix}"))
}

/// Infallible truncate for catalog summaries.
#[must_use]
pub fn truncate_description_or_passthrough(description: &str, max_tokens: usize) -> String {
    truncate_description(description, max_tokens).unwrap_or_else(|_| description.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn count_hello_world() -> Result<(), String> {
        let n = count_tokens("hello world")?;
        assert!((1..=4).contains(&n));
        Ok(())
    }

    #[test]
    fn truncate_respects_budget() -> Result<(), String> {
        let long = "word ".repeat(200);
        let out = truncate_description(&long, 10)?;
        assert!(count_tokens(&out)? <= 10);
        Ok(())
    }
}
