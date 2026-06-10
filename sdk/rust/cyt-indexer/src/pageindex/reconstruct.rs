use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::node_id::node_id_from_value;
use super::parse::extract_skill_prefix;
use super::retrieve::{merge_line_num_specs, merge_node_id_specs, strip_decomposed_frontmatter};
use super::tree::{is_frontmatter_node, is_preamble_node, structure_to_list};
use super::types::{node_md_rel, SkillDocument, SkillsIndex};

/// Subdirectory under a catalog where pruned skill markdown is written.
pub const RETRIEVE_DIR: &str = "skills/retrieve";

/// Options controlling skill markdown reconstruction.
#[derive(Debug, Clone, Copy, Default)]
pub struct ReconstructOptions {
    /// When true, include every document heading in restored markdown; non-matched
    /// sections emit the heading line only (body omitted).
    pub keep_all_headers: bool,
}

#[derive(Debug, Clone)]
pub struct ReconstructResult {
    pub markdown: String,
    pub matched_node_ids: Vec<u32>,
    pub node_ids: Vec<u32>,
    pub output_rel_path: String,
}

/// Collect node ids that directly match line numbers and/or node id specs.
///
/// # Errors
///
/// Returns an error when line or node id specs are invalid.
pub fn collect_matched_node_ids(
    doc: &SkillDocument,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
) -> Result<HashSet<u32>, String> {
    let criteria = parse_match_criteria(line_num_specs, node_id_specs)?;
    let mut matched = HashSet::new();

    for node in structure_to_list(&doc.structure) {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let line_num = node_line_num(obj);
        let node_id = node_id_from_value(obj.get("node_id"));

        if node_matches(&criteria, line_num, node_id) {
            matched.insert(node_id);
        }
    }

    Ok(matched)
}

/// Collect node ids matched by line numbers and/or node ids, including all ancestors.
///
/// # Errors
///
/// Returns an error when line or node id specs are invalid.
pub fn collect_retrieved_node_ids(
    doc: &SkillDocument,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
) -> Result<HashSet<u32>, String> {
    let matched = collect_matched_node_ids(doc, line_num_specs, node_id_specs)?;
    let parent_map = build_parent_map(&doc.structure);
    Ok(expand_with_ancestors(&matched, &parent_map))
}

/// Full content-retrieve payload for JSON output (nodes, restored markdown, node ids).
#[must_use]
pub fn get_content_retrieve_result(
    index: &SkillsIndex,
    doc_id: &str,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
    opts: &ReconstructOptions,
) -> Value {
    let Some(doc) = index.documents.get(doc_id) else {
        return json!({ "error": format!("Document {doc_id} not found") });
    };

    let reconstructed =
        match reconstruct_skill_markdown(index, doc_id, line_num_specs, node_id_specs, opts) {
        Ok(result) => result,
        Err(error) => return json!({ "error": error }),
    };

    let kept: HashSet<u32> = reconstructed.node_ids.iter().copied().collect();
    let pruned = prune_structure(&doc.structure, &kept);
    let nodes = build_restored_nodes(index, doc_id, &pruned);
    let restored_path = format!("{RETRIEVE_DIR}/{}", reconstructed.output_rel_path);

    json!({
        "doc_id": doc_id,
        "matched_node_ids": reconstructed.matched_node_ids,
        "node_ids": reconstructed.node_ids,
        "nodes": nodes,
        "restored_markdown": reconstructed.markdown,
        "restored_path": restored_path,
    })
}

/// Reconstruct pruned skill markdown from retrieve criteria.
///
/// # Errors
///
/// Returns an error when the document is missing or specs are invalid.
pub fn reconstruct_skill_markdown(
    index: &SkillsIndex,
    doc_id: &str,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
    opts: &ReconstructOptions,
) -> Result<ReconstructResult, String> {
    let doc = index
        .documents
        .get(doc_id)
        .ok_or_else(|| format!("Document {doc_id} not found"))?;

    let matched = collect_matched_node_ids(doc, line_num_specs, node_id_specs)?;
    let kept = collect_retrieved_node_ids(doc, line_num_specs, node_id_specs)?;
    let pruned = prune_structure(&doc.structure, &kept);
    let structure_for_assembly = if opts.keep_all_headers {
        &doc.structure
    } else {
        &pruned
    };
    let markdown = assemble_markdown(index, doc, structure_for_assembly, &kept, opts.keep_all_headers);

    let mut matched_node_ids: Vec<u32> = matched.into_iter().collect();
    matched_node_ids.sort_unstable();
    let mut node_ids: Vec<u32> = kept.into_iter().collect();
    node_ids.sort_unstable();

    Ok(ReconstructResult {
        markdown,
        matched_node_ids,
        node_ids,
        output_rel_path: retrieve_output_rel_path(doc),
    })
}

/// Write a reconstructed skill markdown file under `{catalog_dir}/skills/retrieve/`.
///
/// # Errors
///
/// Returns an error when reconstruction fails or the file cannot be written.
pub fn write_reconstructed_skill(
    catalog_dir: &Path,
    index: &SkillsIndex,
    doc_id: &str,
    line_num_specs: &[&str],
    node_id_specs: &[&str],
    opts: &ReconstructOptions,
) -> Result<PathBuf, String> {
    let result = reconstruct_skill_markdown(index, doc_id, line_num_specs, node_id_specs, opts)?;
    let output_path = catalog_dir.join(RETRIEVE_DIR).join(&result.output_rel_path);
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(&output_path, &result.markdown).map_err(|e| e.to_string())?;
    Ok(output_path)
}

#[must_use]
pub fn retrieve_output_rel_path(doc: &SkillDocument) -> String {
    let path = Path::new(&doc.path);
    let file_name = path.file_name().map_or_else(
        || format!("{}.md", doc.doc_name),
        |name| name.to_string_lossy().into_owned(),
    );

    let Some(parent_name) = path.parent().and_then(|parent| parent.file_name()) else {
        return file_name;
    };

    format!("{}/{}", parent_name.to_string_lossy(), file_name)
}

struct MatchCriteria {
    line_set: HashSet<u32>,
    node_set: HashSet<u32>,
    match_by_line: bool,
    match_by_node: bool,
}

fn parse_match_criteria(
    line_num_specs: &[&str],
    node_id_specs: &[&str],
) -> Result<MatchCriteria, String> {
    let line_nums = merge_line_num_specs(line_num_specs)?;
    let node_ids = merge_node_id_specs(node_id_specs)?;

    if line_nums.is_empty() && node_ids.is_empty() {
        return Err("content query requires at least one --line_num or --node_id".to_string());
    }

    let line_set: HashSet<u32> = line_nums.into_iter().collect();
    let node_set: HashSet<u32> = node_ids.into_iter().collect();
    Ok(MatchCriteria {
        match_by_line: !line_set.is_empty(),
        match_by_node: !node_set.is_empty(),
        line_set,
        node_set,
    })
}

fn node_line_num(obj: &serde_json::Map<String, Value>) -> u32 {
    obj.get("line_num")
        .and_then(serde_json::Value::as_u64)
        .map_or(0, |n| u32::try_from(n).unwrap_or(0))
}

fn node_matches(criteria: &MatchCriteria, line_num: u32, node_id: u32) -> bool {
    (criteria.match_by_line && criteria.line_set.contains(&line_num))
        || (criteria.match_by_node && criteria.node_set.contains(&node_id))
}

fn build_restored_nodes(index: &SkillsIndex, doc_id: &str, pruned_structure: &Value) -> Vec<Value> {
    let mut nodes = Vec::new();
    for node in structure_to_list(pruned_structure) {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let content = resolve_node_body(index, doc_id, obj);
        nodes.push(json!({
            "line_num": node_line_num(obj),
            "node_id": node_id_from_value(obj.get("node_id")),
            "content": content,
        }));
    }
    nodes
}

fn build_parent_map(structure: &Value) -> HashMap<u32, u32> {
    let mut map = HashMap::new();
    walk_parent_map(structure, None, &mut map);
    map
}

fn walk_parent_map(node: &Value, parent_id: Option<u32>, map: &mut HashMap<u32, u32>) {
    match node {
        Value::Object(obj) => {
            let node_id = node_id_from_value(obj.get("node_id"));
            if let Some(parent) = parent_id {
                map.insert(node_id, parent);
            }
            if let Some(Value::Array(children)) = obj.get("nodes") {
                for child in children {
                    walk_parent_map(child, Some(node_id), map);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                walk_parent_map(item, parent_id, map);
            }
        }
        _ => {}
    }
}

fn expand_with_ancestors(matched: &HashSet<u32>, parent_map: &HashMap<u32, u32>) -> HashSet<u32> {
    let mut kept = matched.clone();
    for id in matched {
        let mut current = *id;
        while let Some(parent) = parent_map.get(&current) {
            kept.insert(*parent);
            current = *parent;
        }
    }
    kept
}

fn prune_structure(structure: &Value, kept: &HashSet<u32>) -> Value {
    match structure {
        Value::Object(map) => {
            let node_id = node_id_from_value(map.get("node_id"));
            if !kept.contains(&node_id) {
                return Value::Null;
            }

            let mut out = map.clone();
            if let Some(Value::Array(children)) = map.get("nodes") {
                let pruned_children: Vec<Value> = children
                    .iter()
                    .filter_map(|child| {
                        let pruned = prune_structure(child, kept);
                        if pruned.is_null() {
                            None
                        } else {
                            Some(pruned)
                        }
                    })
                    .collect();
                if pruned_children.is_empty() {
                    out.remove("nodes");
                } else {
                    out.insert("nodes".to_string(), Value::Array(pruned_children));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .filter_map(|item| {
                    let pruned = prune_structure(item, kept);
                    if pruned.is_null() {
                        None
                    } else {
                        Some(pruned)
                    }
                })
                .collect(),
        ),
        other => other.clone(),
    }
}

fn structure_has_frontmatter_node(structure: &Value) -> bool {
    structure_to_list(structure)
        .iter()
        .any(|node| node.as_object().is_some_and(is_frontmatter_node))
}

fn structure_has_preamble_node(structure: &Value) -> bool {
    structure_to_list(structure)
        .iter()
        .any(|node| node.as_object().is_some_and(is_preamble_node))
}

fn assemble_markdown(
    index: &SkillsIndex,
    doc: &SkillDocument,
    structure: &Value,
    kept: &HashSet<u32>,
    keep_all_headers: bool,
) -> String {
    let mut parts = Vec::new();

    if !structure_has_frontmatter_node(structure)
        && let Some(frontmatter) = resolve_frontmatter(doc)
    {
        parts.push(frontmatter);
    }

    if !structure_has_preamble_node(structure)
        && let Some(preamble) = resolve_preamble(doc)
    {
        parts.push(preamble);
    }

    for node in structure_to_list(structure) {
        let Some(obj) = node.as_object() else {
            continue;
        };
        let node_id = node_id_from_value(obj.get("node_id"));
        let content = if keep_all_headers && !kept.contains(&node_id) {
            resolve_node_header(index, &doc.id, obj)
        } else {
            resolve_node_body(index, &doc.id, obj)
        };
        if !content.is_empty() {
            parts.push(content);
        }
    }

    parts.join("\n\n")
}

fn resolve_node_header(index: &SkillsIndex, doc_id: &str, node: &serde_json::Map<String, Value>) -> String {
    let body = resolve_node_body(index, doc_id, node);
    if let Some(first_line) = body.lines().next() {
        let trimmed = first_line.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }

    node.get("title")
        .and_then(|v| v.as_str())
        .map(|title| format!("# {title}"))
        .unwrap_or_default()
}

fn resolve_node_body(index: &SkillsIndex, doc_id: &str, node: &serde_json::Map<String, Value>) -> String {
    if let Some(Value::String(text)) = node.get("text")
        && !text.is_empty()
    {
        return text.clone();
    }

    let node_id = node_id_from_value(node.get("node_id"));
    let rel = node_md_rel(doc_id, node_id);
    index
        .files
        .get(&rel)
        .map(|raw| strip_decomposed_frontmatter(raw))
        .unwrap_or_default()
}

fn resolve_frontmatter(doc: &SkillDocument) -> Option<String> {
    if let Some(frontmatter) = &doc.frontmatter {
        return Some(frontmatter.clone());
    }

    let raw = fs::read_to_string(&doc.path).ok()?;
    extract_skill_prefix(&raw).frontmatter
}

fn resolve_preamble(doc: &SkillDocument) -> Option<String> {
    if let Some(preamble) = &doc.preamble {
        return Some(preamble.clone());
    }

    let raw = fs::read_to_string(&doc.path).ok()?;
    extract_skill_prefix(&raw).preamble
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pageindex::{build_skills_index, PageIndexConfig};

    #[test]
    fn node_id_retrieve_includes_parent() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-reconstruct-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("lean-ctx");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("SKILL.md"),
            "---\nname: lean-ctx\ndescription: test\n---\n\n# Root\n\n## Setup\n\nBody\n\n## Other\n\nSkip",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        let doc_id = "lean-ctx__skill";
        let result =
            reconstruct_skill_markdown(&index, doc_id, &[], &["3"], &ReconstructOptions::default())?;

        assert!(result.node_ids.contains(&2), "parent node 2 should be included");
        assert!(result.node_ids.contains(&3), "matched node 3 should be included");
        assert!(!result.node_ids.contains(&4));
        assert!(!result.node_ids.contains(&0), "frontmatter is not an ancestor of content nodes");
        assert!(result.markdown.contains("name: lean-ctx"));
        assert!(result.markdown.contains("# Root"));
        assert!(result.markdown.contains("## Setup"));
        assert!(!result.markdown.contains("## Other"));

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn writes_under_catalog_retrieve_dir() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-reconstruct-write-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("lean-ctx");
        let catalog = tmp.join("catalog");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(skills_dir.join("SKILL.md"), "# Root\n\n## Child\n\nText")
            .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        crate::skills_io::write_skills_index(&index, &catalog)?;

        let output = write_reconstructed_skill(
            &catalog,
            &index,
            "lean-ctx__skill",
            &[],
            &["3"],
            &ReconstructOptions::default(),
        )?;
        assert!(output.ends_with("skills/retrieve/lean-ctx/SKILL.md"));
        let written = fs::read_to_string(&output).map_err(|e| e.to_string())?;
        assert!(written.contains("# Root"));
        assert!(written.contains("## Child"));

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn uses_catalog_frontmatter_over_live_file() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-reconstruct-fm-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("demo");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        let skill_path = skills_dir.join("SKILL.md");
        fs::write(
            &skill_path,
            "---\nname: demo\ndescription: catalog snapshot\n---\n\n# Root\n\n## Child\n\nBody",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        fs::write(
            &skill_path,
            "---\nname: demo\ndescription: live file changed\n---\n\n# Root\n\n## Child\n\nBody",
        )
        .map_err(|e| e.to_string())?;

        let result =
            reconstruct_skill_markdown(&index, "demo__skill", &[], &["3"], &ReconstructOptions::default())?;
        assert!(result.markdown.contains("description: catalog snapshot"));
        assert!(!result.markdown.contains("description: live file changed"));

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn content_retrieve_result_includes_matched_and_restored_nodes() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-retrieve-out-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("lean-ctx");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("SKILL.md"),
            "# Root\n\n## Setup\n\nBody\n\n## Other\n\nSkip",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        let result =
            get_content_retrieve_result(&index, "lean-ctx__skill", &[], &["3"], &ReconstructOptions::default());

        assert_eq!(
            result.get("matched_node_ids").and_then(|v| v.as_array()).map(Vec::len),
            Some(1)
        );
        assert_eq!(
            result
                .get("matched_node_ids")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(serde_json::Value::as_u64),
            Some(3)
        );

        let node_ids: Vec<u32> = result
            .get("node_ids")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_u64().and_then(|n| u32::try_from(n).ok()))
                    .collect()
            })
            .unwrap_or_default();
        assert!(node_ids.contains(&2));
        assert!(node_ids.contains(&3));

        let nodes = result
            .get("nodes")
            .and_then(|v| v.as_array())
            .map_or(0, Vec::len);
        assert_eq!(nodes, 2);

        assert!(result.get("restored_markdown").and_then(|v| v.as_str()).is_some());
        assert_eq!(
            result.get("restored_path").and_then(|v| v.as_str()),
            Some("skills/retrieve/lean-ctx/SKILL.md")
        );

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn keep_all_headers_preserves_unmatched_section_headings() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-reconstruct-headers-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("lean-ctx");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("SKILL.md"),
            "# Root\n\n## Setup\n\nBody\n\n## Other\n\nSkip",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        let default_result =
            reconstruct_skill_markdown(&index, "lean-ctx__skill", &[], &["3"], &ReconstructOptions::default())?;
        assert!(!default_result.markdown.contains("## Other"));

        let kept_headers = reconstruct_skill_markdown(
            &index,
            "lean-ctx__skill",
            &[],
            &["3"],
            &ReconstructOptions {
                keep_all_headers: true,
            },
        )?;
        assert!(kept_headers.markdown.contains("## Setup"));
        assert!(kept_headers.markdown.contains("Body"));
        assert!(kept_headers.markdown.contains("## Other"));
        assert!(!kept_headers.markdown.contains("Skip"));

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn frontmatter_preamble_and_headings_use_reserved_node_ids() -> Result<(), String> {
        let tmp = std::env::temp_dir().join(format!("cyt-reconstruct-preamble-{}", std::process::id()));
        let skills_root = tmp.join("skills");
        let skills_dir = skills_root.join("ctx");
        fs::create_dir_all(&skills_dir).map_err(|e| e.to_string())?;
        fs::write(
            skills_dir.join("SKILL.md"),
            "---\nname: ctx\n---\n\nIntro line\n\n# Root\n\n## Child\n\nBody",
        )
        .map_err(|e| e.to_string())?;

        let index = build_skills_index(&[skills_root], &PageIndexConfig::default())?;
        let doc = index.documents.get("ctx__skill").ok_or("missing doc")?;
        let nodes = structure_to_list(&doc.structure);
        let frontmatter = nodes
            .iter()
            .find(|node| node.as_object().is_some_and(is_frontmatter_node))
            .and_then(|node| node.as_object())
            .ok_or("missing frontmatter node")?;
        assert_eq!(node_id_from_value(frontmatter.get("node_id")), 0);
        assert_eq!(
            frontmatter.get("kind").and_then(|v| v.as_str()),
            Some("frontmatter")
        );
        let frontmatter_md = index
            .files
            .get("skills/decomposed/ctx__skill/0.md")
            .ok_or("missing frontmatter decomposed file")?;
        assert!(frontmatter_md.contains("name: ctx"));

        let preamble = nodes
            .iter()
            .find(|node| node.as_object().is_some_and(is_preamble_node))
            .and_then(|node| node.as_object())
            .ok_or("missing preamble node")?;
        assert_eq!(node_id_from_value(preamble.get("node_id")), 1);
        assert_eq!(preamble.get("kind").and_then(|v| v.as_str()), Some("preamble"));
        assert_eq!(
            preamble.get("line_num").and_then(serde_json::Value::as_u64),
            Some(5)
        );
        let preamble_md = index
            .files
            .get("skills/decomposed/ctx__skill/1.md")
            .ok_or("missing preamble decomposed file")?;
        assert!(preamble_md.contains("Intro line"));

        let first_heading = nodes
            .iter()
            .find(|node| {
                node.as_object().is_some_and(|obj| {
                    !is_frontmatter_node(obj)
                        && !is_preamble_node(obj)
                        && obj.get("title").and_then(|v| v.as_str()) == Some("Root")
                })
            })
            .and_then(|node| node.as_object())
            .ok_or("missing root heading node")?;
        assert_eq!(node_id_from_value(first_heading.get("node_id")), 2);

        assert!(index.files.contains_key("skills/decomposed/ctx__skill/0.md"));
        assert!(index.files.contains_key("skills/decomposed/ctx__skill/1.md"));

        let _ = fs::remove_dir_all(&tmp);
        Ok(())
    }

    #[test]
    fn retrieve_output_rel_path_uses_parent_dir() {
        let doc = SkillDocument {
            id: "lean-ctx__skill".to_string(),
            doc_type: "md".to_string(),
            path: "/Users/me/.claude/skills/lean-ctx/SKILL.md".to_string(),
            doc_name: "SKILL".to_string(),
            line_count: 10,
            structure: Value::Array(vec![]),
            frontmatter: None,
            preamble: None,
        };
        assert_eq!(
            retrieve_output_rel_path(&doc),
            "lean-ctx/SKILL.md"
        );
    }
}
