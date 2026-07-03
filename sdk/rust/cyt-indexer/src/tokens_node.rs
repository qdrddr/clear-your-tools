// N-API bindings for tiktoken token counting (included from `node.rs`).

use crate::tiktoken::{self, AllowedSpecial};

/// Count tokens in text using the configured tiktoken encoding.
///
/// # Errors
///
/// Returns an error when tokenization fails or the count exceeds `u32::MAX`.
#[napi(js_name = "countTokens")]
pub fn count_tokens_napi(text: String) -> Result<u32> {
    let text = text.into_boxed_str();
    let count = tiktoken::count_tokens(text.as_ref()).map_err(Error::from_reason)?;
    u32::try_from(count).map_err(|_| Error::from_reason("token count overflow"))
}

/// Count tokens for a JSON value after compact serialization.
///
/// # Errors
///
/// Returns an error when serialization or tokenization fails.
#[napi(js_name = "countJsonTokens")]
pub fn count_json_tokens_napi(value: Value) -> Result<u32> {
    let value = Box::new(value);
    let count = tiktoken::count_json_tokens(&value).map_err(Error::from_reason)?;
    u32::try_from(count).map_err(|_| Error::from_reason("token count overflow"))
}

/// Override tokenizer defaults in the native core.
#[napi(js_name = "configureTokenizerDefaults")]
pub fn configure_tokenizer_defaults_napi(
    encoding: Option<String>,
    allowed_special: Option<String>,
) {
    let mut cfg = tiktoken::snapshot();
    if let Some(enc) = encoding {
        cfg.encoding = enc;
    }
    if let Some(mode) = allowed_special {
        cfg.allowed_special = match mode.to_ascii_lowercase().as_str() {
            "none" => AllowedSpecial::None,
            _ => AllowedSpecial::All,
        };
    }
    tiktoken::configure(cfg);
}

/// Count tokens for multiple strings in one native call.
///
/// # Errors
///
/// Returns an error when tokenization fails for any input.
#[napi(js_name = "countTokensBatch")]
pub fn count_tokens_batch_napi(texts: Vec<String>) -> Result<Vec<u32>> {
    let boxed: Vec<Box<str>> = texts.into_iter().map(String::into_boxed_str).collect();
    let refs: Vec<&str> = boxed.iter().map(std::convert::AsRef::as_ref).collect();
    let counts = tiktoken::count_tokens_batch(&refs).map_err(Error::from_reason)?;
    counts
        .into_iter()
        .map(|n| u32::try_from(n).map_err(|_| Error::from_reason("token count overflow")))
        .collect()
}
