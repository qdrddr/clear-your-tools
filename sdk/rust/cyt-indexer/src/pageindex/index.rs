use std::path::Path;

use super::config::PageIndexConfig;
use super::parse::{extract_node_text_content, extract_nodes_from_markdown, extract_skill_prefix};
use super::tree::{build_tree_from_nodes, finalize_skill_structure};
use super::types::MdIndexResult;

fn line_count_u32(markdown_content: &str) -> u32 {
    let count = markdown_content.lines().count().max(1);
    u32::try_from(count).unwrap_or(u32::MAX)
}

/// Parse markdown content into a hierarchical document tree.
#[must_use]
pub fn md_to_tree(
    markdown_content: &str,
    source_path: &str,
    config: &PageIndexConfig,
) -> MdIndexResult {
    let line_count = line_count_u32(markdown_content);
    let doc_name = Path::new(source_path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned();

    let prefix = extract_skill_prefix(markdown_content);
    let (node_list, markdown_lines) = extract_nodes_from_markdown(markdown_content);
    let nodes_with_content = extract_node_text_content(&node_list, &markdown_lines);
    let tree_structure = build_tree_from_nodes(&nodes_with_content);
    let structure = finalize_skill_structure(
        tree_structure,
        prefix.frontmatter.as_deref(),
        prefix.frontmatter_line_num,
        prefix.preamble.as_deref(),
        prefix.preamble_line_num,
        config,
    );

    MdIndexResult {
        doc_name,
        line_count,
        structure,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn indexes_simple_markdown() {
        let md = "# Title\n\nBody\n\n## Sub\n\nMore";
        let result = md_to_tree(md, "skill.md", &PageIndexConfig::default());
        assert_eq!(result.doc_name, "skill");
        assert!(result.structure.as_array().is_some_and(|a| !a.is_empty()));
    }
}
