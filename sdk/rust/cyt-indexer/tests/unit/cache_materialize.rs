use cyt_indexer::cache::extract_frontmatter_from_markdown;

#[test]
fn extracts_frontmatter_block() {
    let raw = "---\nname: demo\n---\n# Body";
    assert_eq!(
        extract_frontmatter_from_markdown(raw),
        Some("name: demo".to_string())
    );
}
