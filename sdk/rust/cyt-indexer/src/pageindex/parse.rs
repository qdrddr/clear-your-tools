#[derive(Debug, Clone)]
pub struct HeaderNode {
    pub title: String,
    pub line_num: usize,
}

#[derive(Debug, Clone)]
pub struct ContentNode {
    pub title: String,
    pub line_num: usize,
    pub level: usize,
    pub text: String,
}

/// Parse a markdown heading line (`#`–`######` + title).
#[must_use]
fn parse_header(stripped: &str) -> Option<(usize, &str)> {
    let bytes = stripped.as_bytes();
    if bytes.is_empty() || bytes[0] != b'#' {
        return None;
    }
    let mut level = 0usize;
    while level < bytes.len() && bytes[level] == b'#' {
        level += 1;
    }
    if level == 0 || level > 6 {
        return None;
    }
    if level >= bytes.len() || bytes[level] != b' ' {
        return None;
    }
    let title = stripped[level..].trim();
    if title.is_empty() {
        return None;
    }
    Some((level, title))
}

#[must_use]
pub fn extract_nodes_from_markdown(markdown_content: &str) -> (Vec<HeaderNode>, Vec<String>) {
    let lines: Vec<String> = markdown_content.lines().map(str::to_string).collect();
    let mut node_list = Vec::new();
    let mut in_code_block = false;

    for (idx, line) in lines.iter().enumerate() {
        let line_num = idx + 1;
        let stripped = line.trim();

        if stripped.starts_with("```") {
            in_code_block = !in_code_block;
            continue;
        }

        if stripped.is_empty() || in_code_block {
            continue;
        }

        if let Some((_, title)) = parse_header(stripped) {
            node_list.push(HeaderNode {
                title: title.to_string(),
                line_num,
            });
        }
    }

    (node_list, lines)
}

#[must_use]
pub fn extract_node_text_content(node_list: &[HeaderNode], markdown_lines: &[String]) -> Vec<ContentNode> {
    let mut all_nodes = Vec::new();

    for node in node_list {
        let line_idx = node.line_num.saturating_sub(1);
        let Some(line_content) = markdown_lines.get(line_idx) else {
            continue;
        };
        let Some((level, _)) = parse_header(line_content.trim()) else {
            continue;
        };
        all_nodes.push(ContentNode {
            title: node.title.clone(),
            line_num: node.line_num,
            level,
            text: String::new(),
        });
    }

    let line_nums: Vec<usize> = all_nodes.iter().map(|n| n.line_num).collect();
    for (i, node) in all_nodes.iter_mut().enumerate() {
        let start_line = node.line_num.saturating_sub(1);
        let end_line = if i + 1 < line_nums.len() {
            line_nums[i + 1].saturating_sub(1)
        } else {
            markdown_lines.len()
        };
        node.text = markdown_lines
            .get(start_line..end_line)
            .map(|slice| slice.join("\n").trim().to_string())
            .unwrap_or_default();
    }

    all_nodes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignores_headers_in_code_blocks() {
        let md = "```\n# Not a header\n```\n# Real Header\nBody";
        let (nodes, _) = extract_nodes_from_markdown(md);
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0].title, "Real Header");
    }
}
