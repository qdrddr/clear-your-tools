use cyt_indexer::pageindex::parse::{extract_nodes_from_markdown, extract_skill_prefix};

#[test]
fn extracts_frontmatter_and_preamble() {
    let md = "---\nname: ctx\ndescription: docs\n---\n\nIntro line\n\n## Section\n\nBody";
    let prefix = extract_skill_prefix(md);
    assert_eq!(
        prefix.frontmatter.as_deref(),
        Some("---\nname: ctx\ndescription: docs\n---")
    );
    assert_eq!(prefix.frontmatter_line_num, Some(1));
    assert_eq!(prefix.preamble.as_deref(), Some("Intro line"));
    assert_eq!(prefix.preamble_line_num, Some(6));
}

#[test]
fn ignores_headers_in_code_blocks() {
    let md = "```\n# Not a header\n```\n# Real Header\nBody";
    let (nodes, _) = extract_nodes_from_markdown(md);
    assert_eq!(nodes.len(), 1);
    assert_eq!(nodes[0].title, "Real Header");
}
