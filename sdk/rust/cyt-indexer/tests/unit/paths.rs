use cyt_indexer::paths::{
    PathConfig, configure, decomposed_prefix, default_catalog_dir, home_dir,
    normalize_path_separators, shorten_home_path, tool_id_from_decomposed_rel,
};

#[test]
fn default_prefix_round_trip() {
    let cfg = PathConfig::default();
    configure(cfg);
    let rel = format!("{}tool.json", decomposed_prefix());
    assert_eq!(tool_id_from_decomposed_rel(&rel), "tool");
}

#[test]
fn cyt_default_catalog_dir_is_catalog_not_dot_catalog() {
    let cfg = PathConfig::default();
    assert_eq!(cfg.default_catalog_dir, std::path::PathBuf::from("catalog"));
    configure(cfg);
    assert_eq!(default_catalog_dir(), std::path::PathBuf::from("catalog"));
    assert_eq!(
        chunk_your_skills::paths::default_catalog_dir(),
        std::path::PathBuf::from("catalog")
    );
}

#[test]
fn shorten_home_path_normalizes_separators() -> Result<(), String> {
    let home = home_dir()?;
    let home_norm = normalize_path_separators(&home.to_string_lossy());
    let nested = format!("{home_norm}/.cyt-test/example.md");
    let with_backslashes = nested.replace('/', "\\");
    assert_eq!(
        shorten_home_path(&with_backslashes)?,
        "~/.cyt-test/example.md"
    );
    Ok(())
}
