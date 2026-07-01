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
pub fn extract_node_text_content(
    node_list: &[HeaderNode],
    markdown_lines: &[String],
) -> Vec<ContentNode> {
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

/// YAML frontmatter and optional body text before the first heading.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SkillPrefix {
    pub frontmatter: Option<String>,
    pub frontmatter_line_num: Option<u32>,
    pub preamble: Option<String>,
    pub preamble_line_num: Option<u32>,
}

fn line_num_from_index(index: usize) -> Option<u32> {
    u32::try_from(index + 1).ok()
}

fn extract_preamble_from_lines(lines: &[&str], start_idx: usize) -> (Option<String>, Option<u32>) {
    let mut preamble_lines = Vec::new();
    let mut first_content_line = None;
    let mut in_code_block = false;

    for (offset, line) in lines[start_idx..].iter().enumerate() {
        let line_num = line_num_from_index(start_idx + offset);
        let stripped = line.trim();
        if stripped.starts_with("```") {
            in_code_block = !in_code_block;
            preamble_lines.push(*line);
            continue;
        }
        if !in_code_block && parse_header(stripped).is_some() {
            break;
        }
        if first_content_line.is_none() && !stripped.is_empty() {
            first_content_line = line_num;
        }
        preamble_lines.push(*line);
    }

    let text = preamble_lines.join("\n").trim().to_string();
    if text.is_empty() {
        (None, None)
    } else {
        (Some(text), first_content_line)
    }
}

/// Extract skill YAML frontmatter and preamble (content before the first heading).
#[must_use]
pub fn extract_skill_prefix(markdown_content: &str) -> SkillPrefix {
    let lines: Vec<&str> = markdown_content.lines().collect();
    let mut start_idx = 0usize;
    while start_idx < lines.len() && lines[start_idx].trim().is_empty() {
        start_idx += 1;
    }

    if start_idx >= lines.len() || lines[start_idx].trim() != "---" {
        let (preamble, preamble_line_num) = extract_preamble_from_lines(&lines, 0);
        return SkillPrefix {
            frontmatter: None,
            frontmatter_line_num: None,
            preamble,
            preamble_line_num,
        };
    }

    let frontmatter_line_num = line_num_from_index(start_idx);
    let mut end_idx = start_idx + 1;
    while end_idx < lines.len() && lines[end_idx].trim() != "---" {
        end_idx += 1;
    }
    if end_idx >= lines.len() {
        return SkillPrefix::default();
    }

    let yaml = lines[(start_idx + 1)..end_idx].join("\n");
    let frontmatter = format!("---\n{yaml}\n---");
    let (preamble, preamble_line_num) = extract_preamble_from_lines(&lines, end_idx + 1);

    SkillPrefix {
        frontmatter: Some(frontmatter),
        frontmatter_line_num,
        preamble,
        preamble_line_num,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
