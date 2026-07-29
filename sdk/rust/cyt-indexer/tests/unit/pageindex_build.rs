use std::fs;

use cyt_indexer::pageindex::{
    PageIndexConfig, build_page_index_for_file, build_page_index_only, build_skills_index,
};
use cyt_indexer::paths::home_dir;

#[test]
fn stores_home_paths_with_tilde_prefix() -> Result<(), String> {
    let home = home_dir()?;
    let skills_dir = home.join(format!(".cyt-skills-home-{}", std::process::id()));
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    fs::write(skills_dir.join("example.md"), "# Example\n\nBody").map_err(|e| e.to_string())?;

    let index = build_skills_index(
        std::slice::from_ref(&skills_dir),
        &PageIndexConfig::default(),
    )?;
    let doc = index
        .documents
        .get("example")
        .ok_or_else(|| "missing example document".to_string())?;
    assert_eq!(
        doc.path,
        format!("~/.cyt-skills-home-{}/example.md", std::process::id())
    );

    let _ = fs::remove_dir_all(&skills_dir);
    Ok(())
}

#[test]
fn page_index_only_skips_chunk_files() -> Result<(), String> {
    let home = home_dir()?;
    let skills_dir = home.join(format!(".cyt-skills-page-only-{}", std::process::id()));
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    fs::write(
        skills_dir.join("skill.md"),
        "# Root\n\nBody\n\n## Child\n\nMore",
    )
    .map_err(|e| e.to_string())?;

    let index = build_page_index_only(
        std::slice::from_ref(&skills_dir),
        &PageIndexConfig::default(),
    )?;
    assert!(index.files.keys().all(|k| !k.starts_with("chunks/")));

    let _ = fs::remove_dir_all(&skills_dir);
    Ok(())
}

#[test]
fn build_page_index_for_file_indexes_in_place() -> Result<(), String> {
    let home = home_dir()?;
    let skills_dir = home.join(format!(".cyt-skills-single-{}", std::process::id()));
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    let skill_path = skills_dir.join("create-hook.md");
    fs::write(&skill_path, "# Create Hook\n\nBody").map_err(|e| e.to_string())?;
    fs::write(skills_dir.join("other.md"), "# Other\n\nMore").map_err(|e| e.to_string())?;

    let index = build_page_index_for_file(&skill_path, &PageIndexConfig::default())?;
    assert_eq!(index.documents.len(), 1);
    let doc = index
        .documents
        .get("create-hook")
        .ok_or_else(|| "missing create-hook document".to_string())?;
    assert!(doc.path.ends_with("create-hook.md"));

    let _ = fs::remove_dir_all(&skills_dir);
    Ok(())
}
