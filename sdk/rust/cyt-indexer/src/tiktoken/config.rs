//! Runtime defaults for tiktoken encoding.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AllowedSpecial {
    #[default]
    All,
    None,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TiktokenConfig {
    pub encoding: String,
    pub allowed_special: AllowedSpecial,
}

impl Default for TiktokenConfig {
    fn default() -> Self {
        Self {
            encoding: "cl100k_base".to_string(),
            allowed_special: AllowedSpecial::All,
        }
    }
}

pub fn configure(cfg: TiktokenConfig) {
    super::configure_and_refresh(cfg);
}

#[must_use]
pub fn snapshot() -> TiktokenConfig {
    super::config_snapshot()
}
