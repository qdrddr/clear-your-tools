use clap::{Parser, Subcommand};
use cyt_indexer::{
    apply_per_tool_overrides, build_catalog_from_tools, build_process_groups_options,
    load_catalog_from_dir, parse_tool_policy_pair, per_tool_policies_from_value,
    policy_context_from_values, removed_chunks, retrieve_core, DecomposedCatalog, PolicyContext,
    RemovedChunksOptions, RetrieveOptions, ToolPolicy,
};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;

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
        /// JSON config with pruning.policy / pruning.per_tool (legacy: defaults.*_tool_policy)
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long)]
        system_policy: Option<String>,
        #[arg(long)]
        mcp_policy: Option<String>,
        /// Per-tool policy overrides as JSON object: {"Agent":"always_include",...}
        #[arg(long)]
        per_tool: Option<PathBuf>,
        /// Per-tool override (repeatable): TOOL=POLICY e.g. Agent=always_include
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
    config: Option<&PathBuf>,
    system_policy: Option<&str>,
    mcp_policy: Option<&str>,
    per_tool: Option<&PathBuf>,
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
        ctx.system_policy = ToolPolicy::from_str(s)
            .ok_or_else(|| format!("invalid system policy: {s}"))?;
    }
    if let Some(m) = mcp_policy {
        ctx.mcp_policy = ToolPolicy::from_str(m)
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

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Build { tools, output } => {
            let tools_arr = load_tools_array(&tools)?;
            let index = build_catalog_from_tools(&tools_arr);
            fs::create_dir_all(&output)?;
            for (rel, content) in &index.files {
                let path = output.join(rel);
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::write(path, content)?;
            }
            eprintln!("Wrote {} files to {}", index.files.len(), output.display());
        }
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
            no_score_filter: _no_score_filter,
            removed_output,
        } => {
            let apply_score_filter = score_filter && !_no_score_filter;
            let ctx = policy_context_from_cli(
                config.as_ref(),
                system_policy.as_deref(),
                mcp_policy.as_deref(),
                per_tool.as_ref(),
                &tool_policies,
            )?;
            let catalog_dict = load_catalog_from_dir(catalog.to_str().unwrap())?;
            let mut store = DecomposedCatalog::from_catalog_dict(&catalog_dict);
            let input_raw = fs::read_to_string(input)?;
            let data: Value = serde_json::from_str(&input_raw)?;
            let survivor = DecomposedCatalog::from_catalog_dict(&data);
            let preserve_set = (!preserve.is_empty()).then(|| preserve.into_iter().collect::<HashSet<_>>());
            let process_groups =
                build_process_groups_options(&ctx, &catalog_dict, &store, preserve_set);
            let opts = RetrieveOptions {
                apply_decomposed_score_filter: apply_score_filter,
                process_groups,
            };
            let tools = retrieve_core(&data, &mut store, &survivor, &opts);
            if tools.is_empty() && apply_score_filter {
                eprintln!(
                    "Warning: retrieve produced 0 tools; rerank/pruner survivors usually need \
                     score filter disabled (omit --score-filter)"
                );
            }
            fs::write(output, serde_json::to_string_pretty(&tools)?)?;
            if let Some(removed_path) = removed_output {
                let removed = removed_chunks(
                    &catalog_dict,
                    &data,
                    &RemovedChunksOptions {
                        apply_decomposed_score_filter: apply_score_filter,
                    },
                );
                fs::write(removed_path, serde_json::to_string_pretty(&removed)?)?;
            }
        }
        Commands::Removed {
            catalog,
            input,
            output,
            full,
            score_filter,
        } => {
            let full_catalog = match full {
                Some(path) => {
                    let raw = fs::read_to_string(path)?;
                    serde_json::from_str(&raw)?
                }
                None => load_catalog_from_dir(catalog.to_str().unwrap())?,
            };
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
        }
    }
    Ok(())
}
