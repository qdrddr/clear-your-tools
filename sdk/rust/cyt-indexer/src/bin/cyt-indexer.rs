use clap::{Parser, Subcommand};
use cyt_indexer::{
    apply_per_tool_overrides, build_catalog_from_tools, build_process_groups_options,
    load_catalog_from_dir, parse_tool_policy_pair, per_tool_policies_from_value,
    parse_tool_policy, policy_context_from_values, removed_chunks, retrieve_tools_from_catalog,
    DecomposedCatalog, PolicyContext, RemovedChunksOptions, RetrieveOptions,
};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(name = "cyt-indexer")]
#[command(about = "Tool schema decomposition and catalog indexing")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Build a decomposed catalog index from API tools or catalog entries JSON
    Build {
        #[arg(long)]
        tools: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Retrieve merged tool schemas from pruner output
    Retrieve {
        #[arg(long)]
        catalog: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        /// JSON config with pruning.policy / `pruning.per_tool` (legacy: defaults.*_`tool_policy`)
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long)]
        system_policy: Option<String>,
        #[arg(long)]
        mcp_policy: Option<String>,
        /// Per-tool policy overrides as JSON object: {"`Agent":"always_include`",...}
        #[arg(long)]
        per_tool: Option<PathBuf>,
        /// Per-tool override (repeatable): TOOL=POLICY e.g. `Agent=always_include`
        #[arg(long = "tool-policy", value_name = "TOOL=POLICY")]
        tool_policies: Vec<String>,
        /// Comma-separated enum values to preserve during retrieve (system tools)
        #[arg(long, value_delimiter = ',')]
        preserve: Vec<String>,
        /// Drop json chunks with score <= decomposed threshold (0.5). Off by default; rerank
        /// survivors use tiny scores (~0.003) and need this disabled (matches proxy behavior).
        #[arg(long)]
        score_filter: bool,
        /// Deprecated alias for default behavior (score filter already off unless --score-filter).
        #[arg(long, hide = true)]
        no_score_filter: bool,
        /// Optional path to write non-surviving decomposed chunks (`{json, md}` like survivors.json).
        #[arg(long)]
        removed_output: Option<PathBuf>,
    },
    /// List decomposed chunks in the full catalog that are not in the survivors input.
    Removed {
        /// Full decomposed catalog directory (or use --full for a pre-built catalog JSON).
        #[arg(long)]
        catalog: PathBuf,
        /// Survivors JSON (`{json, md}` arrays), e.g. rerank output or `.catalog/survivors.json`.
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        /// Full catalog JSON instead of walking --catalog (e.g. `decomposed_catalog.build_index`).
        #[arg(long)]
        full: Option<PathBuf>,
        /// Treat json chunks in --input with score <= decomposed threshold as non-surviving.
        #[arg(long)]
        score_filter: bool,
    },
}

fn load_tools_array(tools: &PathBuf) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
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

fn run_build(tools: &Path, output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let tools_arr = load_tools_array(&tools.to_path_buf())?;
    let index = build_catalog_from_tools(&tools_arr);
    fs::create_dir_all(output)?;
    for (rel, content) in &index.files {
        let path = output.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, content)?;
    }
    eprintln!("Wrote {} files to {}", index.files.len(), output.display());
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

fn run_retrieve(args: &RetrieveArgs<'_>) -> Result<(), Box<dyn std::error::Error>> {
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

fn run_removed(
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
        Commands::Build { tools, output } => run_build(&tools, &output)?,
        Commands::Retrieve {
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
            run_retrieve(&retrieve_args)?;
        }
        Commands::Removed {
            catalog,
            input,
            output,
            full,
            score_filter,
        } => run_removed(&catalog, &input, &output, full.as_deref(), score_filter)?,
    }
    Ok(())
}
