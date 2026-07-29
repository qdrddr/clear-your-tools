use cyt_indexer::pageindex::{PageIndexConfig, md_to_tree};

#[test]
fn indexes_simple_markdown() {
    let md = "# Title\n\nBody\n\n## Sub\n\nMore";
    let result = md_to_tree(md, "skill.md", &PageIndexConfig::default());
    assert_eq!(result.doc_name, "skill");
    assert!(result.structure.as_array().is_some_and(|a| !a.is_empty()));
}
