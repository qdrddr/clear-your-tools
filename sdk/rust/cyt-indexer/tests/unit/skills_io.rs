use std::fs;

use cyt_indexer::pageindex::{PageIndexConfig, build_skills_index};
use cyt_indexer::skills_io::{load_skills_index_from_entry, write_skills_index};

#[test]
fn write_and_reconstruct_from_split_index_files() -> Result<(), String> {
    let dir = std::env::temp_dir().join(format!("cyt-skills-{}", std::process::id()));
    let skills_dir = dir.join("skills-src");
    let entry_dir = dir.join("entry");
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
    fs::write(
        skills_dir.join("skill.md"),
        "# Root\n\nBody\n\n## Child\n\nMore",
    )
    .map_err(|e| e.to_string())?;

    let index = build_skills_index(&[skills_dir], &PageIndexConfig::default())?;
    write_skills_index(&index, &entry_dir)?;

    assert!(entry_dir.join("nodes/page_index.json").is_file());
    assert!(
        entry_dir
            .join("chunks/bm25/default/chunk_index.json")
            .is_file()
    );

    let rebuilt = load_skills_index_from_entry(&entry_dir, "skill", None)?;
    assert_eq!(rebuilt.documents.len(), index.documents.len());
    assert!(!rebuilt.files.is_empty());
    let _ = fs::remove_dir_all(&dir);
    Ok(())
}
