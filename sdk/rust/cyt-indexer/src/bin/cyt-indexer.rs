use clap::{Parser, Subcommand};
use cyt_indexer::{
    apply_per_tool_overrides, build_catalog_from_tools, build_process_groups_options,
    get_skill_document, get_skill_line_content, get_skill_structure,
    load_catalog_from_dir, load_skills_index_from_dir, parse_tool_policy_pair,
    per_tool_policies_from_value, parse_tool_policy, policy_context_from_values,
    removed_chunks, retrieve_tools_from_catalog, DecomposedCatalog, PageIndexConfig,
    PolicyContext, RemovedChunksOptions, RetrieveOptions, SkillsBuilder,
};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(name = "cyt-indexer")]
#[command(about = "Tool schema decomposition and skills pageindex catalog indexing")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Build catalogs (tools or skills)
    Build {
        #[command(subcommand)]
        target: BuildTarget,
    },
    /// Retrieve from catalogs (tools or skills)
    Retrieve {
        #[command(subcommand)]
        target: RetrieveTarget,
    },
    /// List removed tool chunks
    Removed {
        #[command(subcommand)]
        target: RemovedTarget,
    },
}

#[derive(Subcommand)]
enum BuildTarget {
    /// Build a decomposed tool catalog from API tools or catalog entries JSON
    Tools {
        #[arg(long)]
        tools: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Build a skills pageindex catalog from markdown skill directories
    Skills {
        #[arg(long = "skills")]
        skill_dirs: Vec<PathBuf>,
        #[arg(long)]
        output: PathBuf,
    },
}

#[derive(Subcommand)]
enum RetrieveTarget {
    /// Retrieve merged tool schemas from pruner output
    Tools {
        #[arg(long)]
        catalog: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long)]
        system_policy: Option<String>,
        #[arg(long)]
        mcp_policy: Option<String>,
        #[arg(long)]
        per_tool: Option<PathBuf>,
        #[arg(long = "tool-policy", value_name = "TOOL=POLICY")]
        tool_policies: Vec<String>,
        #[arg(long, value_delimiter = ',')]
        preserve: Vec<String>,
        #[arg(long)]
        score_filter: bool,
        #[arg(long, hide = true)]
        no_score_filter: bool,
        #[arg(long)]
        removed_output: Option<PathBuf>,
    },
    /// Retrieve skill document metadata, structure, or content
    Skills {
        #[arg(long)]
        catalog: PathBuf,
        #[arg(long)]
        doc_id: String,
        #[arg(long, value_enum)]
        query: SkillQuery,
        #[arg(long = "line_num")]
        line_nums: Vec<String>,
        #[arg(long = "node_id")]
        node_ids: Vec<String>,
        #[arg(long)]
        output: Option<PathBuf>,
        #[arg(long)]
        skills_index: Option<PathBuf>,
    },
}

#[derive(Subcommand)]
enum RemovedTarget {
    /// List decomposed tool chunks not in survivors input
    Tools {
        #[arg(long)]
        catalog: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        full: Option<PathBuf>,
        #[arg(long)]
        score_filter: bool,
    },
}

#[derive(Clone, Copy, clap::ValueEnum)]
enum SkillQuery {
    Metadata,
    Structure,
    Content,
}

fn load_tools_array(tools: &Path) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
    let raw = fs::read_to_string(tools)?;
    let tools_val: Value = serde_json::from_str(&raw)?;
    tools_val
        .as_array()
        .cloned()
        .or_else(|| tools_val.get("tools").and_then(|v| v.as_array()).cloned())
        .ok_or_else(|| "Expected tools array in input JSON".into())
}

fn policy_context_from_cli(
    config: Option<&Path>,
    system_policy: Option<&str>,
    mcp_policy: Option<&str>,
    per_tool: Option<&Path>,
    tool_policies: &[String],
) -> Result<PolicyContext, Box<dyn std::error::Error>> {
    let mut ctx = match config {
        Some(path) => {
            let raw = fs::read_to_string(path)?;
            policy_context_from_values(&serde_json::from_str(&raw)?)
        }
        None => PolicyContext::new(),
    };

    if let Some(s) = system_policy {
        ctx.system_policy = parse_tool_policy(s)
            .ok_or_else(|| format!("invalid system policy: {s}"))?;
    }
    if let Some(m) = mcp_policy {
        ctx.mcp_policy = parse_tool_policy(m)
            .ok_or_else(|| format!("invalid mcp policy: {m}"))?;
    }

    if let Some(path) = per_tool {
        let raw = fs::read_to_string(path)?;
        let val: Value = serde_json::from_str(&raw)?;
        apply_per_tool_overrides(&mut ctx, per_tool_policies_from_value(&val)?);
    }

    let mut cli_overrides = HashMap::new();
    for spec in tool_policies {
        let (tool_id, policy) = parse_tool_policy_pair(spec)?;
        cli_overrides.insert(tool_id, policy);
    }
    apply_per_tool_overrides(&mut ctx, cli_overrides);

    Ok(ctx)
}

fn catalog_path_utf8(catalog: &Path) -> Result<&str, Box<dyn std::error::Error>> {
    catalog.to_str().ok_or_else(|| {
        format!("catalog path is not valid UTF-8: {}", catalog.display()).into()
    })
}

fn run_build_tools(tools: &Path, output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let tools_arr = load_tools_array(tools)?;
    let index = build_catalog_from_tools(&tools_arr);
    fs::create_dir_all(output)?;
    for (rel, content) in &index.files {
        let path = output.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, content)?;
    }
    eprintln!("Wrote {} tool files to {}", index.files.len(), output.display());
    Ok(())
}

fn run_build_skills(skill_dirs: &[PathBuf], output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if skill_dirs.is_empty() {
        return Err("at least one --skills directory is required".into());
    }
    let mut builder = SkillsBuilder::new(false, Some(output.to_path_buf()));
    let config = PageIndexConfig::default();
    builder.build_from_dirs(skill_dirs, &config)?;
    builder.write_catalog()?;
    let Some(index) = builder.index() else {
        return Err("index not built after write_catalog".into());
    };
    eprintln!(
        "Wrote skills catalog ({} documents, {} files) to {}",
        index.documents.len(),
        index.files.len(),
        output.display()
    );
    Ok(())
}

struct RetrieveArgs<'a> {
    catalog: &'a Path,
    input: &'a Path,
    output: &'a Path,
    config: Option<&'a Path>,
    system_policy: Option<&'a str>,
    mcp_policy: Option<&'a str>,
    per_tool: Option<&'a Path>,
    tool_policies: &'a [String],
    preserve: &'a [String],
    score_filter: bool,
    no_score_filter: bool,
    removed_output: Option<&'a Path>,
}

fn run_retrieve_tools(args: &RetrieveArgs<'_>) -> Result<(), Box<dyn std::error::Error>> {
    let apply_score_filter = args.score_filter && !args.no_score_filter;
    let ctx = policy_context_from_cli(
        args.config,
        args.system_policy,
        args.mcp_policy,
        args.per_tool,
        args.tool_policies,
    )?;
    let catalog_dict = load_catalog_from_dir(catalog_path_utf8(args.catalog)?)?;
    let mut store = DecomposedCatalog::from_catalog_dict(&catalog_dict);
    let input_raw = fs::read_to_string(args.input)?;
    let data: Value = serde_json::from_str(&input_raw)?;
    let preserve_set = (!args.preserve.is_empty()).then(|| args.preserve.to_vec());
    let process_groups =
        build_process_groups_options(&ctx, &catalog_dict, &store, preserve_set);
    let opts = RetrieveOptions {
        apply_decomposed_score_filter: apply_score_filter,
        process_groups,
    };
    let tools = retrieve_tools_from_catalog(&ctx, &data, &catalog_dict, &mut store, &opts);
    if tools.is_empty() && apply_score_filter {
        eprintln!(
            "Warning: retrieve produced 0 tools; rerank/pruner survivors usually need \
             score filter disabled (omit --score-filter)"
        );
    }
    fs::write(args.output, serde_json::to_string_pretty(&tools)?)?;
    if let Some(removed_path) = args.removed_output {
        let removed = removed_chunks(
            &catalog_dict,
            &data,
            &RemovedChunksOptions {
                apply_decomposed_score_filter: apply_score_filter,
            },
        );
        fs::write(removed_path, serde_json::to_string_pretty(&removed)?)?;
    }
    Ok(())
}

fn run_retrieve_skills(
    catalog: &Path,
    doc_id: &str,
    query: SkillQuery,
    line_nums: &[String],
    node_ids: &[String],
    output: Option<&Path>,
    skills_index: Option<&Path>,
) -> Result<(), Box<dyn std::error::Error>> {
    let index = if let Some(path) = skills_index {
        let raw = fs::read_to_string(path)?;
        let val: Value = serde_json::from_str(&raw)?;
        let mut idx = cyt_indexer::SkillsIndex::from_skills_index_json(&val)?;
        cyt_indexer::load_decomposed_files_for_index(catalog, &mut idx)?;
        idx
    } else {
        load_skills_index_from_dir(catalog)?
    };

    let result = match query {
        SkillQuery::Metadata => get_skill_document(&index.documents, doc_id),
        SkillQuery::Structure => get_skill_structure(&index.documents, doc_id),
        SkillQuery::Content => {
            if line_nums.is_empty() && node_ids.is_empty() {
                return Err(
                    "content query requires at least one --line_num or --node_id".into(),
                );
            }
            let line_num_specs: Vec<&str> = line_nums.iter().map(String::as_str).collect();
            let node_id_specs: Vec<&str> = node_ids.iter().map(String::as_str).collect();
            get_skill_line_content(&index, doc_id, &line_num_specs, &node_id_specs)
        }
    };

    if result.get("error").is_some() {
        return Err(result
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("retrieve skills failed")
            .into());
    }

    let output_path = output.map_or_else(
        || catalog.join("skill_out.json"),
        Path::to_path_buf,
    );
    fs::write(
        &output_path,
        serde_json::to_string_pretty(&result)?,
    )?;
    Ok(())
}

fn load_full_catalog(
    catalog: &Path,
    full: Option<&Path>,
) -> Result<Value, Box<dyn std::error::Error>> {
    if let Some(path) = full {
        let raw = fs::read_to_string(path)?;
        return Ok(serde_json::from_str(&raw)?);
    }
    load_catalog_from_dir(catalog_path_utf8(catalog)?).map_err(Into::into)
}

fn run_removed_tools(
    catalog: &Path,
    input: &Path,
    output: &Path,
    full: Option<&Path>,
    score_filter: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let full_catalog = load_full_catalog(catalog, full)?;
    let input_raw = fs::read_to_string(input)?;
    let surviving: Value = serde_json::from_str(&input_raw)?;
    let removed = removed_chunks(
        &full_catalog,
        &surviving,
        &RemovedChunksOptions {
            apply_decomposed_score_filter: score_filter,
        },
    );
    fs::write(output, serde_json::to_string_pretty(&removed)?)?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Build { target } => match target {
            BuildTarget::Tools { tools, output } => run_build_tools(&tools, &output)?,
            BuildTarget::Skills {
                skill_dirs,
                output,
            } => run_build_skills(&skill_dirs, &output)?,
        },
        Commands::Retrieve { target } => match target {
            RetrieveTarget::Tools {
                catalog,
                input,
                output,
                config,
                system_policy,
                mcp_policy,
                per_tool,
                tool_policies,
                preserve,
                score_filter,
                no_score_filter,
                removed_output,
            } => {
                let retrieve_args = RetrieveArgs {
                    catalog: &catalog,
                    input: &input,
                    output: &output,
                    config: config.as_deref(),
                    system_policy: system_policy.as_deref(),
                    mcp_policy: mcp_policy.as_deref(),
                    per_tool: per_tool.as_deref(),
                    tool_policies: &tool_policies,
                    preserve: &preserve,
                    score_filter,
                    no_score_filter,
                    removed_output: removed_output.as_deref(),
                };
                run_retrieve_tools(&retrieve_args)?;
            }
            RetrieveTarget::Skills {
                catalog,
                doc_id,
                query,
                line_nums,
                node_ids,
                output,
                skills_index,
            } => run_retrieve_skills(
                &catalog,
                &doc_id,
                query,
                &line_nums,
                &node_ids,
                output.as_deref(),
                skills_index.as_deref(),
            )?,
        },
        Commands::Removed { target } => match target {
            RemovedTarget::Tools {
                catalog,
                input,
                output,
                full,
                score_filter,
            } => run_removed_tools(&catalog, &input, &output, full.as_deref(), score_filter)?,
        },
    }
    Ok(())
}
