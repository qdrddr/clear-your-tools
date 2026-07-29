//! Unified path configuration — delegates tools paths to `chunk-your-tools` and syncs skills paths.

pub use chunk_your_tools::paths::{
    PathConfig, collect_enums, decomposed_prefix, decomposed_root, default_catalog_dir,
    get_root_tool_key, home_dir, is_catalog_decomposed_path, json_ext, md_ext,
    normalize_path_separators, skills_decomposed_prefix, snapshot, to_decomposed_key,
    to_skills_decomposed_key, tool_id_from_decomposed_rel,
};

pub use chunk_your_tools::paths::{
    builder_memory_only, catalog_prefix, expand_home_path, shorten_home_path, write_catalog_prune,
};

#[must_use]
pub fn skills_decomposed_root() -> std::path::PathBuf {
    snapshot().skills_decomposed_root
}

fn sync_skills_paths(cfg: &PathConfig) {
    chunk_your_skills::paths::configure(chunk_your_skills::paths::PathConfig {
        md_ext: cfg.md_ext.clone(),
        skills_decomposed_prefix: cfg.skills_decomposed_prefix.clone(),
        skills_decomposed_root: cfg.skills_decomposed_root.clone(),
        default_catalog_dir: cfg.default_catalog_dir.clone(),
    });
}

pub fn configure(cfg: PathConfig) {
    sync_skills_paths(&cfg);
    chunk_your_tools::paths::configure(cfg);
}
