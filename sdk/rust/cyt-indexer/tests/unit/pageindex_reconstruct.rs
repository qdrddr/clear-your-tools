#![allow(clippy::unwrap_used)]

use std::fs;

use cyt_indexer::pageindex::node_id::node_id_from_value;
use cyt_indexer::pageindex::tree::{is_frontmatter_node, is_preamble_node, structure_to_list};
use cyt_indexer::pageindex::{
    PageIndexConfig, ReconstructOptions, SkillDocument, build_skills_index,
    get_content_retrieve_result, reconstruct_skill_markdown, retrieve_output_rel_path,
    write_reconstructed_skill,
};
use cyt_indexer::skills_io::write_skills_index;
use serde_json::Value;

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
    let result = reconstruct_skill_markdown(
        &index,
        doc_id,
        &[],
        &["3"],
        &[],
        &ReconstructOptions::default(),
    )?;

    assert!(
        result.node_ids.contains(&2),
        "parent node 2 should be included"
    );
    assert!(
        result.node_ids.contains(&3),
        "matched node 3 should be included"
    );
    assert!(!result.node_ids.contains(&4));
    assert!(
        !result.node_ids.contains(&0),
        "frontmatter is not an ancestor of content nodes"
    );
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
    write_skills_index(&index, &catalog)?;

    let output = write_reconstructed_skill(
        &catalog,
        &index,
        "lean-ctx__skill",
        &[],
        &["3"],
        &[],
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

    let result = reconstruct_skill_markdown(
        &index,
        "demo__skill",
        &[],
        &["3"],
        &[],
        &ReconstructOptions::default(),
    )?;
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
    let result = get_content_retrieve_result(
        &index,
        "lean-ctx__skill",
        &[],
        &["3"],
        &[],
        &ReconstructOptions::default(),
    );

    assert_eq!(
        result
            .get("matched_node_ids")
            .and_then(|v| v.as_array())
            .map(Vec::len),
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

    assert!(
        result
            .get("restored_markdown")
            .and_then(|v| v.as_str())
            .is_some()
    );
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
    let default_result = reconstruct_skill_markdown(
        &index,
        "lean-ctx__skill",
        &[],
        &["3"],
        &[],
        &ReconstructOptions::default(),
    )?;
    assert!(!default_result.markdown.contains("## Other"));

    let kept_headers = reconstruct_skill_markdown(
        &index,
        "lean-ctx__skill",
        &[],
        &["3"],
        &[],
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
        .get("nodes/n0.md")
        .ok_or("missing frontmatter decomposed file")?;
    assert!(frontmatter_md.contains("name: ctx"));

    let preamble = nodes
        .iter()
        .find(|node| node.as_object().is_some_and(is_preamble_node))
        .and_then(|node| node.as_object())
        .ok_or("missing preamble node")?;
    assert_eq!(node_id_from_value(preamble.get("node_id")), 1);
    assert_eq!(
        preamble.get("kind").and_then(|v| v.as_str()),
        Some("preamble")
    );
    assert_eq!(
        preamble.get("line_num").and_then(serde_json::Value::as_u64),
        Some(5)
    );
    let preamble_md = index
        .files
        .get("nodes/n1.md")
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

    assert!(index.files.contains_key("nodes/n0.md"));
    assert!(index.files.contains_key("nodes/n1.md"));

    let _ = fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn chunk_retrieve_omits_preamble_when_chunk_one_not_selected() -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!(
        "cyt-reconstruct-chunk-preamble-{}",
        std::process::id()
    ));
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
    let preamble_chunks: Vec<u32> = structure_to_list(&doc.structure)
        .iter()
        .filter(|node| node.as_object().is_some_and(is_preamble_node))
        .flat_map(|node| {
            node.as_object()
                .and_then(|obj| obj.get("chunks"))
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
                .filter_map(|chunk| {
                    chunk
                        .as_object()
                        .and_then(|o| o.get("chunk_id"))
                        .and_then(serde_json::Value::as_u64)
                        .and_then(|n| u32::try_from(n).ok())
                })
                .collect::<Vec<_>>()
        })
        .collect();
    assert!(
        preamble_chunks.contains(&1),
        "expected preamble chunk id 1, got {preamble_chunks:?}"
    );

    let content_chunks: Vec<u32> = structure_to_list(&doc.structure)
        .iter()
        .filter(|node| {
            node.as_object().is_some_and(|obj| {
                !is_frontmatter_node(obj)
                    && !is_preamble_node(obj)
                    && obj.get("chunks").and_then(|v| v.as_array()).is_some()
            })
        })
        .flat_map(|node| {
            node.as_object()
                .and_then(|obj| obj.get("chunks"))
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
                .filter_map(|chunk| {
                    chunk
                        .as_object()
                        .and_then(|o| o.get("chunk_id"))
                        .and_then(serde_json::Value::as_u64)
                        .and_then(|n| u32::try_from(n).ok())
                })
                .filter(|id| *id != 1)
                .collect::<Vec<_>>()
        })
        .collect();
    assert!(
        !content_chunks.is_empty(),
        "expected at least one non-preamble chunk"
    );

    let chunk_specs: Vec<String> = content_chunks.iter().map(ToString::to_string).collect();
    let chunk_refs: Vec<&str> = chunk_specs.iter().map(String::as_str).collect();
    let result = reconstruct_skill_markdown(
        &index,
        "ctx__skill",
        &[],
        &[],
        &chunk_refs,
        &ReconstructOptions::default(),
    )?;

    assert!(
        !result.node_ids.contains(&1),
        "preamble node should not be kept"
    );
    assert!(!result.matched_chunk_ids.contains(&1));
    assert!(!result.markdown.contains("Intro line"));
    assert!(result.markdown.contains("Body"));

    let with_preamble = reconstruct_skill_markdown(
        &index,
        "ctx__skill",
        &[],
        &[],
        &["1"],
        &ReconstructOptions::default(),
    )?;
    assert!(with_preamble.node_ids.contains(&1));
    assert!(with_preamble.markdown.contains("Intro line"));

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
    assert_eq!(retrieve_output_rel_path(&doc), "lean-ctx/SKILL.md");
}
