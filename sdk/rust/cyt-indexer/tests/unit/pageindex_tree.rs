use cyt_indexer::pageindex::parse::{extract_node_text_content, extract_nodes_from_markdown};
use cyt_indexer::pageindex::tree::build_tree_from_nodes;

#[test]
fn nested_headings_form_tree() {
    let md = "# Root\n\n## Child\n\nText\n\n# Second";
    let (headers, lines) = extract_nodes_from_markdown(md);
    let nodes = extract_node_text_content(&headers, &lines);
    let tree = build_tree_from_nodes(&nodes);
    let arr = tree.as_array();
    assert!(arr.is_some_and(|items| !items.is_empty()));
    let first = arr
        .and_then(|items| items.first())
        .and_then(|v| v.as_object());
    assert!(first.is_some_and(|obj| obj.contains_key("nodes")));
}
