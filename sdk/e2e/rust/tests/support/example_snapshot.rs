use cyt_indexer::build_catalog_index;
use serde_json::{json, Value};
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

pub fn parse_test_args() -> (Option<String>, Option<String>) {
    let file = std::env::var("CYT_E2E_FILE")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("FILE").ok().filter(|s| !s.is_empty()));
    let output = std::env::var("CYT_E2E_OUTPUT")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("OUTPUT").ok().filter(|s| !s.is_empty()));
    if file.is_some() || output.is_some() {
        return (file, output);
    }

    let args: Vec<String> = std::env::args().collect();
    let mut file = None;
    let mut output = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--file" if i + 1 < args.len() => {
                i += 1;
                file = Some(args[i].clone());
            }
            s if let Some(path) = s.strip_prefix("--file=") => {
                file = Some(path.to_string());
            }
            "--output" if i + 1 < args.len() => {
                i += 1;
                output = Some(args[i].clone());
            }
            s if let Some(path) = s.strip_prefix("--output=") => {
                output = Some(path.to_string());
            }
            _ => {}
        }
        i += 1;
    }
    (file, output)
}

pub fn resolve_snapshot_path(path: &str) -> PathBuf {
    let candidate = PathBuf::from(path);
    if candidate.is_file() {
        return candidate;
    }
    let from_repo = repo_root().join(path);
    if from_repo.is_file() {
        return from_repo;
    }
    panic!("snapshot file not found: {path} (also tried {})", from_repo.display());
}

pub fn load_snapshot(path: &Path) -> Value {
    let raw = fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    serde_json::from_str(&raw).unwrap_or_else(|e| panic!("parse {}: {e}", path.display()))
}

fn enums_from_md(md_entries: &[Value]) -> Vec<Value> {
    md_entries
        .iter()
        .filter_map(|entry| entry.get("content").and_then(|v| v.as_str()))
        .map(|s| json!(s))
        .collect()
}

fn survivor_catalog(stage: &Value) -> Value {
    let mut survivor = json!({});
    if let Some(json_entries) = stage.get("json") {
        survivor["json"] = json_entries.clone();
    }
    if let Some(md_entries) = stage.get("md") {
        survivor["md"] = md_entries.clone();
    }
    survivor
}

pub fn extract_snapshot_parts(data: &Value) -> (Vec<Value>, Value, Vec<Value>) {
    let pruning = data.get("pruning").unwrap_or(&Value::Null);
    let stages = pruning
        .get("decomposed_catalog")
        .unwrap_or(&Value::Null);

    let (expected, build_stage, survivor_stage) = if data.get("body").is_some() {
        let expected = data["body"]
            .get("tools")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let build_stage = stages
            .get("build_index")
            .cloned()
            .unwrap_or(Value::Null);
        let survivor_stage = stages
            .get("rerank")
            .cloned()
            .unwrap_or_else(|| build_stage.clone());
        (expected, build_stage, survivor_stage)
    } else {
        let expected = data
            .get("tools")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let build_stage = stages
            .get("build_index")
            .cloned()
            .unwrap_or(Value::Null);
        let has_survivor = stages.get("json").is_some() || stages.get("md").is_some();
        let survivor_stage = if has_survivor {
            stages.clone()
        } else {
            build_stage.clone()
        };
        (expected, build_stage, survivor_stage)
    };

    let build_tools = build_stage
        .get("tools")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if build_tools.is_empty() && !expected.is_empty() {
        panic!(
            "snapshot has no pruning.decomposed_catalog.build_index.tools; cannot rebuild catalog index"
        );
    }

    let survivor = survivor_catalog(&survivor_stage);
    let has_json = survivor
        .get("json")
        .and_then(|v| v.as_array())
        .is_some_and(|a| !a.is_empty());
    let has_md = survivor
        .get("md")
        .and_then(|v| v.as_array())
        .is_some_and(|a| !a.is_empty());
    if !has_json && !has_md {
        panic!("snapshot has no rerank json/md entries for decomposition");
    }

    (build_tools, survivor, expected)
}

pub fn catalog_dict_from_snapshot(data: &Value) -> Value {
    let (build_tools, _survivor, _expected) = extract_snapshot_parts(data);
    let build_stage = data
        .pointer("/pruning/decomposed_catalog/build_index")
        .unwrap_or(&Value::Null);
    let md_entries = build_stage
        .get("md")
        .and_then(|v| v.as_array())
        .map(|a| a.as_slice())
        .unwrap_or(&[]);
    let enums = enums_from_md(md_entries);
    let index = build_catalog_index(&build_tools, &enums);
    index.to_catalog_dict("src/catalog")
}

pub fn write_output(catalog: &Value, output_path: Option<&str>) -> io::Result<()> {
    let payload = serde_json::to_string_pretty(catalog)? + "\n";
    match output_path {
        Some(path) => {
            let out = Path::new(path);
            if let Some(parent) = out.parent() {
                if !parent.as_os_str().is_empty() {
                    fs::create_dir_all(parent)?;
                }
            }
            fs::write(out, payload)
        }
        None => {
            io::stdout().write_all(payload.as_bytes())?;
            Ok(())
        }
    }
}
